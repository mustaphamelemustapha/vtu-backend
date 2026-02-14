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
    payment_methods = [m.strip() for m in settings.monnify_payment_methods.split(",") if m.strip()]
    payload = {
        "amount": amount,
        "customerName": name or email,
        "customerEmail": email,
        "paymentReference": reference,
        "paymentDescription": "Wallet funding",
        "currencyCode": settings.monnify_currency,
        "contractCode": settings.monnify_contract_code,
        "redirectUrl": callback_url,
        "paymentMethods": payment_methods,
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


def reserve_monnify_account(
    *,
    account_reference: str,
    account_name: str,
    customer_email: str,
    customer_name: str,
    bvn: str | None = None,
    nin: str | None = None,
    get_all_available_banks: bool = True,
) -> dict:
    token = get_monnify_token()
    payload = {
        "accountReference": account_reference,
        "accountName": account_name,
        "currencyCode": settings.monnify_currency,
        "contractCode": settings.monnify_contract_code,
        "customerEmail": customer_email,
        "customerName": customer_name or customer_email,
        "getAllAvailableBanks": bool(get_all_available_banks),
    }
    if bvn:
        payload["bvn"] = bvn
    if nin:
        payload["nin"] = nin

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    with httpx.Client(timeout=20) as client:
        resp = client.post(f"{settings.monnify_base_url}/api/v2/bank-transfer/reserved-accounts", json=payload, headers=headers)
        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except Exception:
                detail = {"message": resp.text}
            raise ValueError(f"Monnify reserve account failed: {detail}")
        return resp.json()


def get_reserved_account_details(*, account_reference: str) -> dict:
    token = get_monnify_token()
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=20) as client:
        resp = client.get(f"{settings.monnify_base_url}/api/v2/bank-transfer/reserved-accounts/{account_reference}", headers=headers)
        if resp.status_code == 404:
            return {"__not_found__": True}
        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except Exception:
                detail = {"message": resp.text}
            raise ValueError(f"Monnify fetch reserved account failed: {detail}")
        return resp.json()


def verify_monnify_signature(body: bytes, signature: str) -> bool:
    secret = settings.monnify_webhook_secret or settings.monnify_secret_key
    sig = (signature or "").strip()
    if not sig:
        return False
    # Monnify docs often specify SHA512(secret + body), but some integrations use HMAC-SHA512.
    computed_concat = hashlib.sha512(secret.encode() + body).hexdigest()
    computed_hmac = hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(computed_concat, sig) or hmac.compare_digest(computed_hmac, sig)
