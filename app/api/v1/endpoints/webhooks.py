import logging
from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.orm import Session
from decimal import Decimal

from app.core.database import get_db
from app.models import Transaction, TransactionStatus
from app.services.wallet import get_or_create_wallet, credit_wallet
from app.core.config import get_settings

router = APIRouter()
logger = logging.getLogger(__name__)
settings = get_settings()

@router.post("/smeplug")
async def smeplug_webhook(request: Request, db: Session = Depends(get_db)):
    """
    SMEPlug Webhook Handler
    
    Payload format:
    {
        "transaction": {
            "status": "success" | "failed",
            "reference": "<provider_reference>",
            "customer_reference": "<internal_transaction_id>",
            "beneficiary": "<phone>",
            "price": "<amount>"
        }
    }
    """
    # Optional webhook token validation (supports either bearer auth or x-webhook-token).
    configured_secret = str(settings.smeplug_webhook_secret or "").strip()
    if configured_secret:
        auth_header = str(request.headers.get("authorization") or "")
        token_header = str(request.headers.get("x-webhook-token") or "")
        bearer = auth_header.replace("Bearer ", "").strip() if auth_header.lower().startswith("bearer ") else auth_header.strip()
        if configured_secret not in {bearer, token_header.strip()}:
            raise HTTPException(status_code=401, detail="Invalid SMEPlug webhook token")

    payload = await request.json()
    logger.info("SMEPlug Webhook received: %s", payload)
    
    tx_data = payload.get("transaction") or payload.get("data") or payload
    if not tx_data:
        logger.warning("SMEPlug Webhook missing transaction data")
        return {"status": "ignored"}

    status = str(tx_data.get("status") or "").strip().lower()
    customer_reference = tx_data.get("customer_reference")
    provider_reference = tx_data.get("reference")
    
    if not customer_reference:
        logger.warning("SMEPlug Webhook missing customer_reference")
        return {"status": "ignored"}

    # Find transaction using customer_reference
    transaction = db.query(Transaction).filter(Transaction.reference == customer_reference).first()
    if not transaction:
        logger.warning("SMEPlug Webhook: Transaction not found for ref %s", customer_reference)
        return {"status": "ignored"}

    # Idempotency check: if already success or refunded, ignore
    if transaction.status in {TransactionStatus.SUCCESS, TransactionStatus.REFUNDED}:
        logger.info("SMEPlug Webhook: Transaction %s already in terminal state %s", customer_reference, transaction.status)
        return {"status": "ok"}

    if status == "success":
        transaction.status = TransactionStatus.SUCCESS
        transaction.external_reference = provider_reference
        db.commit()
        logger.info("SMEPlug Webhook: Transaction %s marked as SUCCESS", customer_reference)
    elif status == "failed":
        transaction.status = TransactionStatus.FAILED
        transaction.external_reference = provider_reference
        
        # Refund wallet
        wallet = get_or_create_wallet(db, transaction.user_id)
        credit_wallet(
            db, 
            wallet, 
            Decimal(transaction.amount), 
            transaction.reference, 
            f"Refund for failed SMEPlug data purchase (Ref: {provider_reference})"
        )
        transaction.status = TransactionStatus.REFUNDED
        db.commit()
        logger.info("SMEPlug Webhook: Transaction %s marked as FAILED and REFUNDED", customer_reference)
    else:
        logger.info("SMEPlug Webhook: Transaction %s status is %s, keeping pending", customer_reference, status)

    return {"status": "ok"}
