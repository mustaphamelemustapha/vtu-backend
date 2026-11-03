import hashlib
import hmac
import time
import httpx
from app.core.config import get_settings


settings = get_settings()


class PaystackError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, data: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.data = data or {}


from app.core.database import SessionLocal
from app.models.integration import PaymentGateway

def _headers() -> dict:
    db = SessionLocal()
    try:
        db_settings = db.query(PaymentGateway).filter(PaymentGateway.identifier == "paystack").first()
        if db_settings and db_settings.is_active:
            secret = db_settings.secret_key or settings.paystack_secret_key
        else:
            secret = settings.paystack_secret_key
    finally:
        db.close()
    return {"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}


def _request(method: str, path: str, payload: dict | None = None, params: dict | None = None) -> dict:
    url = f"https://api.paystack.co{path}"
    
    max_retries = 3
    last_exception = None
    res = None
    
    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=45) as client:
                res = client.request(method, url, json=payload, params=params, headers=_headers())
            break
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.NetworkError) as exc:
            last_exception = exc
            if attempt < max_retries - 1:
                time.sleep(1.0 * (attempt + 1))
            else:
                raise httpx.ReadTimeout(f"Paystack request timed out after {max_retries} attempts: {exc}") from exc
                
    if res is None:
        raise last_exception or RuntimeError("Request failed without response")
        
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

    db = SessionLocal()
    try:
        db_settings = db.query(PaymentGateway).filter(PaymentGateway.identifier == "paystack").first()
        db_secret = db_settings.secret_key if db_settings and db_settings.is_active else None
    finally:
        db.close()
        
    if db_secret and db_secret not in candidates:
        candidates.append(db_secret)

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


_BANK_CACHE = {"timestamp": 0, "banks": []}

POPULAR_NIGERIAN_BANKS = [
    {"name": "Access Bank", "code": "044", "slug": "access-bank"},
    {"name": "Guaranty Trust Bank (GTBank)", "code": "058", "slug": "guaranty-trust-bank"},
    {"name": "Zenith Bank", "code": "057", "slug": "zenith-bank"},
    {"name": "United Bank for Africa (UBA)", "code": "033", "slug": "united-bank-for-africa"},
    {"name": "First Bank of Nigeria", "code": "011", "slug": "first-bank-of-nigeria"},
    {"name": "OPay (Paycom)", "code": "999992", "slug": "paycom"},
    {"name": "PalmPay", "code": "999991", "slug": "palmpay"},
    {"name": "Moniepoint MFB", "code": "50515", "slug": "moniepoint-mfb-ng"},
    {"name": "Kuda Bank", "code": "50211", "slug": "kuda-bank"},
    {"name": "Fidelity Bank", "code": "070", "slug": "fidelity-bank"},
    {"name": "Stanbic IBTC Bank", "code": "221", "slug": "stanbic-ibtc-bank"},
    {"name": "Sterling Bank", "code": "232", "slug": "sterling-bank"},
    {"name": "Union Bank of Nigeria", "code": "032", "slug": "union-bank-of-nigeria"},
    {"name": "Wema Bank (ALAT)", "code": "035", "slug": "wema-bank"},
    {"name": "First City Monument Bank (FCMB)", "code": "214", "slug": "first-city-monument-bank"},
    {"name": "Polaris Bank", "code": "076", "slug": "polaris-bank"},
    {"name": "Keystone Bank", "code": "082", "slug": "keystone-bank"},
    {"name": "Jaiz Bank", "code": "301", "slug": "jaiz-bank"},
    {"name": "TAJ Bank", "code": "302", "slug": "taj-bank"},
    {"name": "VFD Microfinance Bank", "code": "566", "slug": "vfd"},
    {"name": "FairMoney Microfinance Bank", "code": "51318", "slug": "fairmoney-mfb"},
    {"name": "Rubies MFB", "code": "125", "slug": "rubies-mfb"},
    {"name": "Carbon", "code": "565", "slug": "carbon"},
    {"name": "Titan Trust Bank", "code": "102", "slug": "titan-bank"},
    {"name": "Providus Bank", "code": "101", "slug": "providus-bank"},
]


def list_nigerian_banks() -> list[dict]:
    """
    Returns list of Nigerian banks from Paystack, falling back to curated list if API fails.
    Results are cached in-memory for 1 hour.
    """
    global _BANK_CACHE
    now = time.time()
    if _BANK_CACHE["banks"] and (now - _BANK_CACHE["timestamp"] < 3600):
        return _BANK_CACHE["banks"]

    try:
        resp = _request("GET", "/bank", params={"country": "nigeria", "perPage": 100})
        banks_data = resp.get("data") or []
        if banks_data:
            formatted = [
                {
                    "name": b.get("name"),
                    "code": str(b.get("code") or "").strip(),
                    "slug": b.get("slug"),
                    "active": b.get("active", True),
                }
                for b in banks_data
                if b.get("name") and b.get("code")
            ]
            formatted.sort(key=lambda x: x["name"])
            _BANK_CACHE = {"timestamp": now, "banks": formatted}
            return formatted
    except Exception:
        pass

    return POPULAR_NIGERIAN_BANKS


def resolve_bank_account(account_number: str, bank_code: str) -> dict:
    """
    Resolves account number against bank code using Paystack.
    Returns: {"account_number": str, "account_name": str, "bank_id": int}
    """
    clean_acc = str(account_number or "").strip()
    clean_code = str(bank_code or "").strip()
    resp = _request("GET", "/bank/resolve", params={"account_number": clean_acc, "bank_code": clean_code})
    data = resp.get("data") or {}
    if not data.get("account_name"):
        raise PaystackError("Could not resolve account name. Please verify bank and account number.")
    return data


def create_transfer_recipient(name: str, account_number: str, bank_code: str, currency: str = "NGN") -> dict:
    """
    Creates a Paystack transfer recipient (NUBAN).
    Returns dict containing 'recipient_code' (e.g. RCP_...).
    """
    payload = {
        "type": "nuban",
        "name": str(name).strip(),
        "account_number": str(account_number).strip(),
        "bank_code": str(bank_code).strip(),
        "currency": currency,
    }
    resp = _request("POST", "/transferrecipient", payload=payload)
    return resp.get("data") or resp


def initiate_transfer(amount_kobo: int, recipient_code: str, reference: str, reason: str = "Wallet withdrawal") -> dict:
    """
    Initiates a transfer from Paystack balance to a recipient.
    """
    payload = {
        "source": "balance",
        "amount": amount_kobo,
        "recipient": recipient_code,
        "reference": reference,
        "reason": reason,
    }
    resp = _request("POST", "/transfer", payload=payload)
    return resp.get("data") or resp


def verify_paystack_transfer(reference: str) -> dict:
    """
    Verifies transfer status via Paystack API.
    """
    resp = _request("GET", f"/transfer/verify/{reference}")
    return resp.get("data") or resp


def get_paystack_balance() -> float:
    """
    Fetches the Paystack balance for transfers (in NGN).
    Paystack returns amount in kobo, so we divide by 100.
    """
    try:
        resp = _request("GET", "/balance")
        data = resp.get("data")
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("currency") == "NGN":
                    return float(item.get("balance", 0)) / 100.0
        return 0.0
    except Exception as exc:
        raise PaystackError(f"Failed to fetch Paystack balance: {exc}")

