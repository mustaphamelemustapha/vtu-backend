import hashlib
import hmac
import httpx
from app.core.config import get_settings


settings = get_settings()


def create_paystack_checkout(email: str, amount_kobo: int, reference: str, callback_url: str) -> dict:
    payload = {
        "email": email,
        "amount": amount_kobo,
        "reference": reference,
        "callback_url": callback_url,
    }
    headers = {"Authorization": f"Bearer {settings.paystack_secret_key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=15) as client:
        response = client.post("https://api.paystack.co/transaction/initialize", json=payload, headers=headers)
        response.raise_for_status()
        return response.json()


def verify_paystack_signature(body: bytes, signature: str) -> bool:
    secret = settings.paystack_webhook_secret or settings.paystack_secret_key
    computed = hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(computed, signature)
