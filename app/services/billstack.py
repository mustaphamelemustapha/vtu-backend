import hmac
import hashlib
import httpx
import logging
from app.core.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

def generate_billstack_virtual_account(*, email: str, reference: str, phone: str, first_name: str, last_name: str, bank: str) -> dict:
    url = "https://api.billstack.co/v2/thirdparty/generateVirtualAccount/"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.billstack_api_key}",
    }
    payload = {
        "email": email,
        "reference": reference,
        "firstName": first_name or "Customer",
        "lastName": last_name or "Customer",
        "phone": phone or "",
        "bank": bank,
    }
    logger.info("Generating Billstack account for %s. Bank: %s, Ref: %s", email, bank, reference)
    with httpx.Client(timeout=20) as client:
        resp = client.post(url, json=payload, headers=headers)
        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except Exception:
                detail = {"message": resp.text}
            raise ValueError(f"Billstack generate account failed: {detail}")
        return resp.json()

def upgrade_billstack_kyc(*, email: str, bvn: str) -> dict:
    url = "https://api.billstack.co/v2/thirdparty/upgradeVirtualAccount/"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.billstack_api_key}",
    }
    payload = {
        "customer": email,
        "bvn": bvn,
    }
    logger.info("Upgrading Billstack virtual account KYC for email: %s", email)
    with httpx.Client(timeout=20) as client:
        resp = client.post(url, json=payload, headers=headers)
        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except Exception:
                detail = {"message": resp.text}
            raise ValueError(f"Billstack KYC upgrade failed: {detail}")
        return resp.json()

def verify_billstack_signature(body: bytes, signature: str) -> bool:
    sig = (signature or "").strip()
    if not sig:
        return False
    
    candidates = []
    if settings.billstack_webhook_secret:
        candidates.append(settings.billstack_webhook_secret)
    if settings.billstack_api_key and settings.billstack_api_key not in candidates:
        candidates.append(settings.billstack_api_key)
        
    for secret in candidates:
        if sig == secret:
            return True
        for algo in [hashlib.sha512, hashlib.sha256]:
            computed = hmac.new(secret.encode(), body, algo).hexdigest()
            if hmac.compare_digest(computed, sig):
                return True
    return False
