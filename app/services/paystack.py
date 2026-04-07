import hashlib
import hmac
import httpx
from app.core.config import get_settings


settings = get_settings()


class PaystackError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, data: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.data = data or {}


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.paystack_secret_key}", "Content-Type": "application/json"}


def _request(method: str, path: str, payload: dict | None = None, params: dict | None = None) -> dict:
    url = f"https://api.paystack.co{path}"
    with httpx.Client(timeout=20) as client:
        res = client.request(method, url, json=payload, params=params, headers=_headers())
    data = res.json() if res.content else {}
    if res.status_code >= 400:
        message = data.get("message") or data.get("error") or "Paystack error"
        raise PaystackError(message, status_code=res.status_code, data=data)
    return data


def create_paystack_checkout(email: str, amount_kobo: int, reference: str, callback_url: str) -> dict:
    payload = {
        "email": email,
        "amount": amount_kobo,
        "reference": reference,
        "callback_url": callback_url,
    }
    return _request("POST", "/transaction/initialize", payload=payload)


def verify_paystack_signature(body: bytes, signature: str) -> bool:
    # Paystack signs webhooks with the secret key. Some deployments also set a
    # dedicated webhook secret env var; accept either configured value.
    candidates = []
    if settings.paystack_webhook_secret:
        candidates.append(settings.paystack_webhook_secret)
    if settings.paystack_secret_key and settings.paystack_secret_key not in candidates:
        candidates.append(settings.paystack_secret_key)

    for secret in candidates:
        computed = hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()
        if hmac.compare_digest(computed, signature):
            return True
    return False


def verify_paystack_transaction(reference: str) -> dict:
    return _request("GET", f"/transaction/verify/{reference}")


def create_paystack_customer(email: str, first_name: str, last_name: str, phone: str | None = None) -> dict:
    payload = {"email": email, "first_name": first_name, "last_name": last_name}
    if phone:
        payload["phone"] = phone
    try:
        return _request("POST", "/customer", payload=payload)
    except PaystackError as exc:
        message = str(exc).lower()
        if "already exists" in message or "duplicate" in message:
            return _request("GET", f"/customer/{email}")
        raise


def update_paystack_customer(
    customer_code: str,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    phone: str | None = None,
) -> dict:
    payload: dict = {}
    if first_name:
        payload["first_name"] = first_name
    if last_name:
        payload["last_name"] = last_name
    if phone:
        payload["phone"] = phone
    if not payload:
        return {}
    return _request("PUT", f"/customer/{customer_code}", payload=payload)


def get_paystack_customer(email_or_code: str) -> dict:
    return _request("GET", f"/customer/{email_or_code}")


def list_dedicated_accounts(customer_code: str) -> dict:
    return _request("GET", "/dedicated_account", params={"customer": customer_code})


def create_dedicated_account(customer_code: str, preferred_bank: str | None = None) -> dict:
    payload: dict = {"customer": customer_code}
    if preferred_bank:
        payload["preferred_bank"] = preferred_bank
    return _request("POST", "/dedicated_account", payload=payload)


def get_or_create_dedicated_account(email: str, first_name: str, last_name: str, phone: str | None = None) -> dict:
    customer_resp = create_paystack_customer(email, first_name, last_name, phone)
    customer = customer_resp.get("data") or {}
    customer_code = customer.get("customer_code") or customer.get("id")
    if not customer_code:
        raise PaystackError("Unable to resolve Paystack customer code", data=customer_resp)

    # Existing customers may have been created without phone; ensure it is present.
    if phone and not (customer.get("phone") or "").strip():
        try:
            updated = update_paystack_customer(
                str(customer_code),
                first_name=first_name,
                last_name=last_name,
                phone=phone,
            )
            maybe_customer = (updated or {}).get("data") or {}
            if maybe_customer:
                customer = maybe_customer
        except PaystackError:
            # Dedicated account creation below will surface a precise provider error if any.
            pass

    try:
        existing = list_dedicated_accounts(customer_code)
        data = existing.get("data") or []
        if data:
            return data[0]
    except PaystackError:
        # Continue to create if listing fails.
        pass

    created = create_dedicated_account(customer_code, settings.paystack_dedicated_preferred_bank)
    return created.get("data") or created
