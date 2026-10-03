import httpx
import logging
from app.core.config import get_settings

logger = logging.getLogger(__name__)

def send_termii_sms(phone_number: str, message: str) -> bool:
    """
    Sends an SMS using the Termii Messaging API.
    Ensure phone_number is in international format (e.g., '2348030000000').
    """
    settings = get_settings()
    api_key = settings.termii_api_key
    base_url = settings.termii_base_url.rstrip("/")
    sender_id = settings.termii_sender_id

    if not api_key:
        logger.warning("Termii API key is not configured, skipping SMS.")
        return False

    url = f"{base_url}/api/sms/send"
    
    # Clean phone number (strip '+' if present)
    clean_phone = phone_number.lstrip('+')

    payload = {
        "to": clean_phone,
        "from": sender_id,
        "sms": message,
        "type": "plain",
        "channel": "dnd", # Use dnd channel for OTPs in Nigeria
        "api_key": api_key,
    }

    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            logger.info("Termii SMS sent successfully: %s", data.get("message_id"))
            return True
    except httpx.HTTPError as exc:
        logger.error(f"Termii SMS failed: {exc}")
        if hasattr(exc, 'response') and exc.response is not None:
            logger.error(f"Response: {exc.response.text}")
        return False
