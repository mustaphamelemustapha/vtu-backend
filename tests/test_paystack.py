import json
from app.services.paystack import verify_paystack_signature
from app.core.config import get_settings


def test_paystack_signature():
    settings = get_settings()
    body = json.dumps({"event": "charge.success", "data": {"reference": "ABC"}}).encode()
    signature = __import__("hmac").new(settings.paystack_webhook_secret.encode(), body, __import__("hashlib").sha512).hexdigest()
    assert verify_paystack_signature(body, signature)
