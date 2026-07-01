import json
import hmac
import hashlib
import logging
import asyncio
import httpx
from typing import Dict, Any, Union
from datetime import datetime, timezone
from app.models.user import User
from app.models.transaction import Transaction
from app.models.service_transaction import ServiceTransaction

logger = logging.getLogger(__name__)

async def _send_webhook_async(url: str, payload: Dict[str, Any], signature: str):
    """Fires the webhook asynchronously."""
    headers = {
        "Content-Type": "application/json",
        "X-Mele-Signature": signature
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, headers=headers, timeout=10.0)
            if response.status_code >= 400:
                logger.warning(f"Outbound webhook to {url} failed with status {response.status_code}: {response.text}")
            else:
                logger.info(f"Outbound webhook to {url} succeeded with status {response.status_code}")
    except Exception as e:
        logger.error(f"Failed to send outbound webhook to {url}: {e}")

def dispatch_developer_webhook(transaction: Union[Transaction, ServiceTransaction], user: User):
    """
    Dispatches a webhook to the developer's configured webhook_url 
    when a transaction status updates.
    """
    if not getattr(user, "is_developer", False) or not getattr(user, "webhook_url", None):
        return

    url = user.webhook_url.strip()
    if not url:
        return

    # Handle enum values if needed
    status_str = transaction.status.value if hasattr(transaction.status, "value") else str(transaction.status)

    # Strip developer prefix if it exists
    original_ref = transaction.reference
    prefix = f"DEV_{user.id}_"
    if original_ref.startswith(prefix):
        original_ref = original_ref[len(prefix):]

    # Build the payload
    payload = {
        "event": "transaction.updated",
        "data": {
            "reference": original_ref,
            "status": "delivered" if status_str == "success" else "failed",
            "amount": float(transaction.amount),
            "network": transaction.provider or getattr(transaction, "network", None),
            "mobile_number": getattr(transaction, "recipient_phone", None) or getattr(transaction, "customer", None),
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z"
        }
    }
    
    # Calculate signature
    secret = getattr(user, "webhook_secret", "") or ""
    payload_bytes = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    signature = hmac.new(secret.encode('utf-8'), payload_bytes, hashlib.sha512).hexdigest()

    # Fire and forget (create task in current running event loop)
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_send_webhook_async(url, payload, signature))
    except RuntimeError:
        # No running event loop (e.g. running in synchronous context)
        asyncio.run(_send_webhook_async(url, payload, signature))
    
    logger.info(f"Dispatched webhook for transaction {transaction.reference} to developer {user.id}")
