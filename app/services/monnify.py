import base64
import hashlib
import hmac
import httpx
from app.core.config import get_settings

settings = get_settings()


def _basic_auth() -> str:
    token = f"{settings.monnify_api_key}:{settings.monnify_secret_key}"
    return base64.b64encode(token.encode()).decode()


def get_monnify_token() -> str:
    headers = {
        "Authorization": f"Basic {_basic_auth()}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=15) as client:
        resp = client.post(f"{settings.monnify_base_url}/api/v1/auth/login", headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data.get("responseBody", {}).get("accessToken")


def init_monnify_transaction(email: str, name: str, amount: float, reference: str, callback_url: str) -> dict:
    token = get_monnify_token()
    payload = {
        "amount": amount,
        "customerName": name or email,
        "customerEmail": email,
        "paymentReference": reference,
        "paymentDescription": "Wallet funding",
        "currencyCode": "NGN",
        "contractCode": settings.monnify_contract_code,
        "redirectUrl": callback_url,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=15) as client:
        resp = client.post(
            f"{settings.monnify_base_url}/api/v1/merchant/transactions/init-transaction",
            json=payload,
            headers=headers,
        )
        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except Exception:
                detail = {"message": resp.text}
            raise ValueError(f"Monnify init failed: {detail}")
        return resp.json()


def verify_monnify_signature(body: bytes, signature: str) -> bool:
    secret = settings.monnify_webhook_secret or settings.monnify_secret_key
    computed = hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(computed, signature)
