import hmac
import hashlib
import httpx
import logging
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.integration import PaymentGateway

settings = get_settings()
logger = logging.getLogger(__name__)

def _get_api_key():
    db = SessionLocal()
    try:
        db_settings = db.query(PaymentGateway).filter(
            PaymentGateway.identifier.in_(["billstack", "BILLSTACK", "Billstack"])
        ).first()
        if db_settings:
            key = (db_settings.secret_key or db_settings.public_key or "").strip()
            if key:
                return key
        return (getattr(settings, "billstack_api_key", "") or "").strip()
    finally:
        db.close()

def generate_billstack_virtual_account(
    *,
    email: str,
    reference: str,
    phone: str,
    first_name: str,
    last_name: str,
    bank: str,
    id_type: str | None = None,
    id_number: str | None = None,
) -> dict:
    url = "https://api.billstack.co/v2/thirdparty/generateVirtualAccount/"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_get_api_key()}",
    }
    payload = {
        "email": email,
        "reference": reference,
        "firstName": first_name or "Customer",
        "lastName": last_name or "Customer",
        "phone": (phone or "08012345678").strip(),
        "bank": bank,
    }
    if id_number:
        clean_id = str(id_number).strip()
        payload["bvn"] = clean_id
        payload["licenseNumber"] = clean_id
        payload["idNumber"] = clean_id
        if id_type:
            payload["idType"] = id_type
            if id_type.lower() == "nin":
                payload["nin"] = clean_id
                payload["identityType"] = "personal_nin"
            else:
                payload["identityType"] = "personal"

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

def upgrade_billstack_kyc(*, email: str, bvn: str, nin: str | None = None) -> dict:
    url = "https://api.billstack.co/v2/thirdparty/upgradeVirtualAccount/"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_get_api_key()}",
    }
    payload = {
        "customer": email,
        "bvn": bvn,
    }
    if nin:
        payload["nin"] = nin
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

def _get_candidate_secrets() -> list[str]:
    candidates: list[str] = []
    for val in [
        getattr(settings, "billstack_webhook_secret", ""),
        getattr(settings, "billstack_api_key", ""),
    ]:
        v = (val or "").strip()
        if v and v not in candidates:
            candidates.append(v)

    db = SessionLocal()
    try:
        db_settings = db.query(PaymentGateway).filter(
            PaymentGateway.identifier.in_(["billstack", "BILLSTACK", "Billstack"])
        ).first()
        if db_settings:
            for val in [db_settings.secret_key, db_settings.public_key, db_settings.contract_code]:
                v = (val or "").strip()
                if v and v not in candidates:
                    candidates.append(v)
    except Exception:
        pass
    finally:
        db.close()
    return candidates


def verify_billstack_signature(body: bytes, signature: str, timestamp: str | None = None) -> bool:
    sig = (signature or "").strip()
    if not sig:
        return False
    sig_lower = sig.lower()

    candidates = _get_candidate_secrets()
    for secret in candidates:
        if sig == secret or sig_lower == secret.lower():
            return True
        # Billstack / Wiaxy standard webhook header: x-wiaxy-signature = md5(secret_key)
        for digest_fn in [hashlib.md5, hashlib.sha256, hashlib.sha512]:
            hashed_secret = digest_fn(secret.encode()).hexdigest().lower()
            if hmac.compare_digest(hashed_secret, sig_lower):
                return True
        # HMAC over raw body (and optional timestamp.body)
        payloads = [body]
        if timestamp:
            payloads.append(f"{timestamp}.".encode() + body)
        for p in payloads:
            for algo in [hashlib.sha512, hashlib.sha256, hashlib.md5]:
                computed = hmac.new(secret.encode(), p, algo).hexdigest().lower()
                if hmac.compare_digest(computed, sig_lower):
                    return True
    return False
