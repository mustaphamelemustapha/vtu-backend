import hashlib
import logging
import re
import secrets
import time
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.config import get_settings
from app.dependencies import get_current_user, require_admin
from app.models import User, UserRole, DataPlan, Transaction, TransactionStatus, TransactionType, ApiLog
from app.schemas.data import DataPlanOut, BuyDataRequest
from app.services.amigo import (
    AmigoClient,
    AmigoApiError,
    canonical_plan_code,
    normalize_plan_code,
    resolve_network_id,
    split_plan_code,
)
from app.providers.smeplug_provider import SMEPlugProvider
from app.services.bills import get_bills_provider
from app.services.fraud import enforce_purchase_limits
from app.services.wallet import get_or_create_wallet, debit_wallet, credit_wallet
from app.services.pricing import get_price_for_user
from app.middlewares.rate_limit import limiter
from app.utils.cache import get_cached, set_cached

router = APIRouter()
settings = get_settings()
logger = logging.getLogger(__name__)

_PLAN_CACHE_VERSION = "v5"


_SUCCESS_STATUS = {"success", "successful", "delivered", "completed", "ok", "done"}
_PENDING_STATUS = {"pending", "processing", "queued", "in_progress", "accepted", "submitted"}
_FAILURE_STATUS = {"failed", "fail", "error", "rejected", "declined", "cancelled", "canceled", "refunded"}

_SUCCESS_HINTS = ("successfully", "delivered", "gifted", "completed")
_FAILURE_HINTS = ("failed", "unsuccessful", "unable", "error", "rejected", "declined", "cancelled", "canceled")
_PENDING_HINTS = ("pending", "processing", "queued", "in progress", "submitted")
_PENDING_PROVIDER_HINTS = (
    "already processing",
    "check your wallet before trying again",
    "please wait a moment",
)
_DEFINITIVE_FAILURE_CODES = {
    "invalid_token",
    "plan_not_found",
    "insufficient_balance",
    "invalid_network",
    "invalid_phone",
    "coming_soon",
    "transaction_failed",
}
_DEFINITIVE_FAILURE_HINTS = (
    "invalid token",
    "plan not found",
    "insufficient balance",
    "invalid network",
    "invalid phone",
    "coming soon",
    "transaction failed",
)
_VTPASS_DATA_NETWORKS = {"9mobile", "etisalat", "t2"}
_AMIGO_DATA_NETWORKS = {"mtn", "glo"}
_SMEPLUG_DATA_NETWORKS = {"airtel"}
_CURATED_SIZE_TARGETS_GB = (0.2, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0, 15.0, 20.0)
_CURATED_MAX_PER_NETWORK = 8
_CURATED_FILTER_KEYWORDS = (
    "night",
    "social",
    "weekend",
    "daily",
    "2day",
    "2-day",
    "1day",
    "1-day",
    "awoof",
    "bonus",
    "router",
    "mifi",
    "youtube",
    "unlimited",
)

_AIRTEL_BUNDLE_ORDER = {
    "500MB": 0,
    "1GB": 1,
    "2GB": 2,
    "3GB": 3,
    "4GB": 4,
    "10GB": 5,
    "18GB": 6,
    "25GB": 7,
}

_AIRTEL_VALIDITY_TARGETS = {
    "500MB": 7,
    "1GB": 30,
    "2GB": 30,
    "3GB": 30,
    "4GB": 30,
    "10GB": 30,
    "18GB": 30,
    "25GB": 30,
}

_AIRTEL_AMIGO_ALLOWED_CODES = {
    "airtel:163",
    "airtel:145",
    "airtel:146",
    "airtel:532",
    "airtel:148",
    "airtel:150",
    "airtel:405",
    "airtel:404",
}

_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(GB|MB)", re.IGNORECASE)
_VALIDITY_RE = re.compile(r"(\d+)\s*(d|day|days|month|months|week|weeks)", re.IGNORECASE)
_AMIGO_PENDING_CONFIRM_ATTEMPTS = 6
_AMIGO_PENDING_CONFIRM_DELAY_SECONDS = 1.4


def _is_airtel_row(row: DataPlan) -> bool:
    return str(row.network or "").strip().lower() == "airtel"


def _is_smeplug_airtel_row(row: DataPlan) -> bool:
    if not _is_airtel_row(row):
        return False
    return str(row.provider or "").strip().lower() == "smeplug"


def _deactivate_non_smeplug_airtel_rows(db: Session) -> int:
    rows = (
        db.query(DataPlan)
        .filter(
            func.lower(DataPlan.network) == "airtel",
            DataPlan.is_active == True,
        )
        .all()
    )
    changed = 0
    for row in rows:
        if str(row.provider or "").strip().lower() != "smeplug":
            row.is_active = False
            changed += 1
    return changed


def _promo_plan_code_suffix(plan_code: str | None) -> str:
    raw = str(plan_code or "").strip().lower()
    if not raw:
        return ""
    return raw.split(":")[-1] if ":" in raw else raw


def _is_mtn_1gb_promo_plan(plan: DataPlan) -> bool:
    if not settings.promo_mtn_1gb_enabled:
        return False
    network = str(plan.network or "").strip().lower()
    if network != str(settings.promo_mtn_1gb_network or "mtn").strip().lower():
        return False
    return _promo_plan_code_suffix(plan.plan_code) == str(settings.promo_mtn_1gb_plan_code or "1001").strip().lower()


def _count_mtn_1gb_promo_successes(db: Session) -> int:
    network = str(settings.promo_mtn_1gb_network or "mtn").strip().lower()
    suffix = str(settings.promo_mtn_1gb_plan_code or "1001").strip().lower()
    count = (
        db.query(func.count(func.distinct(Transaction.user_id)))
        .filter(
            Transaction.tx_type == TransactionType.DATA,
            Transaction.status == TransactionStatus.SUCCESS,
            func.lower(Transaction.network) == network,
            or_(
                func.lower(Transaction.data_plan_code) == suffix,
                func.lower(Transaction.data_plan_code).like(f"%:{suffix}"),
            ),
        )
        .scalar()
    )
    return int(count or 0)


def _mtn_1gb_promo_snapshot(db: Session) -> dict[str, object]:
    limit = max(int(settings.promo_mtn_1gb_limit or 0), 0)
    if not settings.promo_mtn_1gb_enabled or limit <= 0:
        return {"active": False, "remaining": 0, "limit": limit, "price": None}
    consumed = _count_mtn_1gb_promo_successes(db)
    remaining = max(limit - consumed, 0)
    return {
        "active": remaining > 0,
        "remaining": remaining,
        "limit": limit,
        "price": Decimal(str(settings.promo_mtn_1gb_price or "199")),
    }


def _user_has_used_mtn_1gb_promo(db: Session, user_id: int) -> bool:
    network = str(settings.promo_mtn_1gb_network or "mtn").strip().lower()
    suffix = str(settings.promo_mtn_1gb_plan_code or "1001").strip().lower()
    promo_price = Decimal(str(settings.promo_mtn_1gb_price or "199"))
    row = (
        db.query(Transaction.id)
        .filter(
            Transaction.user_id == int(user_id),
            Transaction.tx_type == TransactionType.DATA,
            Transaction.status == TransactionStatus.SUCCESS,
            func.lower(Transaction.network) == network,
            or_(
                func.lower(Transaction.data_plan_code) == suffix,
                func.lower(Transaction.data_plan_code).like(f"%:{suffix}"),
            ),
            Transaction.amount == promo_price,
        )
        .first()
    )
    return bool(row)


def _safe_reason(value: str, limit: int = 255) -> str:
    text = str(value or "").strip()
    return text[:limit] if text else "Unknown provider error"


def _build_amigo_failure_reason(exc: AmigoApiError) -> str:
    """
    Convert provider exceptions into user/admin-friendly, supportable reasons.
    """
    message = _safe_reason(getattr(exc, "message", "") or "")
    raw_text = str(getattr(exc, "raw", "") or "").strip()
    status_code = getattr(exc, "status_code", None)

    generic_markers = {"transaction failed", "failed", "error", "unknown provider error"}
    if message.lower() in generic_markers and raw_text:
        # Try to salvage a clearer reason from raw provider payload/text.
        cleaned_raw = raw_text.replace("\n", " ").replace("\r", " ").strip()
        if cleaned_raw:
            message = _safe_reason(cleaned_raw)

    if status_code:
        # Prefix with provider HTTP status for faster admin diagnosis.
        message = _safe_reason(f"Provider rejected request (HTTP {status_code}): {message}")

    return message


def _is_ambiguous_provider_error(exc: AmigoApiError) -> bool:
    """
    Returns True when provider outcome is uncertain and we must NOT auto-refund.
    Example: vendor accepted order but returned non-JSON/plain-text response.
    """
    msg = str(getattr(exc, "message", "") or "").strip().lower()
    status = getattr(exc, "status_code", None)
    if status is not None and 200 <= int(status) < 300:
        return True
    ambiguous_hints = (
        "non-json",
        "invalid json",
        "unexpected response",
        "temporarily unavailable",
        "remote protocol",
        "timeout",
        "timed out",
        "unable to reach data provider",
        "unable to reach provider",
        "connection reset",
        "connection error",
        "connection aborted",
        "service unavailable",
        "already processing",
        "check your wallet before trying again",
        "please wait a moment",
    )
    return any(hint in msg for hint in ambiguous_hints)


def _client_request_reference(prefix: str, user_id: int, request_id: str | None) -> str | None:
    raw = str(request_id or "").strip()
    if not raw:
        return None
    digest = hashlib.sha256(f"{prefix}:{user_id}:{raw}".encode()).hexdigest()[:24].upper()
    return f"{prefix}_{digest}"


def _is_vtpass_network(network: str | None) -> bool:
    key = str(network or "").strip().lower()
    return key in _VTPASS_DATA_NETWORKS


def _is_amigo_data_network(network: str | None) -> bool:
    key = str(network or "").strip().lower()
    return key in _AMIGO_DATA_NETWORKS


def _is_mtn_network(network: str | None) -> bool:
    key = str(network or "").strip().lower()
    return key in {"mtn", "1", "01"}


def _extract_capacity(value: str | None) -> str:
    if not value:
        return ""
    match = _SIZE_RE.search(value)
    if not match:
        return ""
    amount = match.group(1)
    unit = match.group(2).upper()
    return f"{amount}{unit}"


def _extract_validity(value: str | None) -> str:
    if not value:
        return ""
    match = _VALIDITY_RE.search(value)
    if not match:
        return ""
    amount = int(match.group(1))
    unit = match.group(2).lower()
    if unit.startswith("month"):
        return f"{amount * 30}d"
    if unit.startswith("week"):
        return f"{amount * 7}d"
    return f"{amount}d"


def _clean_plan_label(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    cleaned = re.sub(r"\(\s*direct\s+data\s*\)", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bdirect\s+data\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s*-\s*$", "", cleaned)
    cleaned = re.sub(r"^\s*-\s*", "", cleaned)
    cleaned = cleaned.strip(" -")
    return cleaned or text


def _parse_size_gb(value: str | None) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = _SIZE_RE.search(text)
    if not match:
        return None
    amount = float(match.group(1))
    unit = match.group(2).upper()
    if unit == "MB":
        return amount / 1024.0
    return amount


def _upsert_plan_from_provider(db: Session, item: dict) -> bool:
    network = str(item.get("network") or "").strip().lower()
    if not network:
        return False

    incoming_code = str(item.get("plan_code") or item.get("provider_plan_id") or item.get("id") or "").strip()
    if not incoming_code:
        return False

    canonical_code = canonical_plan_code(network, incoming_code)
    if not canonical_code:
        return False

    plan = db.query(DataPlan).filter(DataPlan.plan_code == canonical_code).first()
    
    clean_plan_name = _clean_plan_label(str(item.get("plan_name") or item.get("name") or "").strip())[:255]
    clean_data_size = str(item.get("data_size") or item.get("size") or "").strip()[:255] or "—"
    clean_validity = str(item.get("validity") or "").strip()[:64]
    clean_provider = str(item.get("provider") or "")[:64]
    clean_provider_plan_id = str(item.get("provider_plan_id") or "")[:64]

    if not plan:
        plan = DataPlan(
            network=network,
            plan_code=canonical_code,
            plan_name=clean_plan_name,
            data_size=clean_data_size,
            validity=clean_validity,
            base_price=Decimal(str(item.get("price") or item.get("cost_price") or "0")),
            provider=clean_provider,
            provider_plan_id=clean_provider_plan_id,
            is_active=True,
        )
        db.add(plan)
        return True

    plan.network = network
    plan.plan_name = clean_plan_name or plan.plan_name
    plan.data_size = clean_data_size or plan.data_size
    plan.validity = clean_validity or plan.validity
    plan.base_price = Decimal(str(item.get("price") or item.get("cost_price") or plan.base_price))
    plan.provider = clean_provider or plan.provider
    plan.provider_plan_id = clean_provider_plan_id or plan.provider_plan_id
    return True


def _invalidate_plans_cache() -> None:
    for role in (UserRole.USER.value, UserRole.RESELLER.value, UserRole.ADMIN.value):
        pass


def _refresh_provider_network_plans(db: Session, provider, network: str) -> tuple[int, set[str]]:
    if not hasattr(provider, "fetch_data_variations"):
        return 0, set()
    variations = provider.fetch_data_variations(network)
    if not isinstance(variations, list):
        return 0, set()

    touched = 0
    active_codes: set[str] = set()
    items: list[dict] = []
    for variation in variations:
        item = _provider_variation_to_item(network, variation)
        if not item:
            continue
        items.append(item)

    for item in items:
        canonical_code = canonical_plan_code(network, str(item.get("plan_code") or ""))
        if canonical_code:
            active_codes.add(canonical_code)
        touched += 1 if _upsert_plan_from_provider(db, item) else 0

    if variations:
        stale_rows = (
            db.query(DataPlan)
            .filter(func.lower(DataPlan.network) == network.lower(), DataPlan.is_active == True)
            .all()
        )
        for row in stale_rows:
            code = str(row.plan_code or "").strip()
            if code not in active_codes:
                row.is_active = False
                touched += 1

    return touched, active_codes


@router.get("/plans", response_model=list[DataPlanOut])
def list_data_plans(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # cache_key = f"plans:{_PLAN_CACHE_VERSION}:{user.role.value}"
    # cached = get_cached(cache_key)
    # if cached:
    #     return cached

    plans = db.query(DataPlan).filter(DataPlan.is_active == True).all()
    logger.info("Initial active plans count: %d", len(plans))

    # Check which networks are represented
    # Calculate initial breakdown to decide if sync is needed
    breakdown = {}
    for p in plans:
        nw = str(p.network or "unknown").lower()
        breakdown[nw] = breakdown.get(nw, 0) + 1
    # --- STRICT PROVIDER ROUTING SYNC ---
    
    # 1. MTN & GLO -> Amigo (ONLY)
    if breakdown.get("mtn", 0) < 25 or breakdown.get("glo", 0) < 15:
        logger.info("MTN/Glo plans low. Syncing from Amigo...")
        try:
            from app.services.amigo import AmigoClient
            client = AmigoClient()
            response = client.fetch_data_plans()
            items = response.get("data", [])
            if items:
                touched = 0
                for item in items:
                    nw = str(item.get("network") or "").lower()
                    if nw in {"mtn", "glo"}:
                        item["provider"] = "amigo"
                        touched += 1 if _upsert_plan_from_provider(db, item) else 0
                if touched:
                    db.commit()
                    logger.info("Amigo sync finished (MTN/Glo). Touched %d plans.", touched)
        except Exception as exc:
            logger.warning("Amigo sync failed: %s. Check your AMIGO_BASE_URL and AMIGO_API_KEY.", exc)

    # 2. AIRTEL -> SMEPlug (ONLY)
    # Always keep Airtel synced from SMEPlug
    logger.info("Syncing Airtel plans from SMEPlug...")
    try:
        smeplug = SMEPlugProvider()
        airtel_items = smeplug.get_airtel_plans()
        if airtel_items:
            touched = 0
            for item in airtel_items:
                item["provider"] = "smeplug"
                touched += 1 if _upsert_plan_from_provider(db, item) else 0
            if touched:
                db.commit()
                logger.info("SMEPlug sync finished (Airtel). Touched %d plans.", touched)
    except Exception as exc:
        logger.warning("SMEPlug Airtel sync failed: %s", exc)

    # 3. 9MOBILE -> ClubKonnect/Bills (ONLY)
    if breakdown.get("9mobile", 0) < 10:
        logger.info("9mobile plans low. Syncing from Bills Provider...")
        try:
            provider = get_bills_provider()
            if hasattr(provider, "fetch_data_variations"):
                items = provider.fetch_data_variations("9mobile")
                if items:
                    touched = 0
                    for item in items:
                        item["network"] = "9mobile"
                        # Bills providers are often ClubKonnect
                        item["provider"] = str(getattr(provider, "name", "clubkonnect")).lower()
                        touched += 1 if _upsert_plan_from_provider(db, item) else 0
                    if touched:
                        db.commit()
                        logger.info("9mobile sync finished. Touched %d plans.", touched)
        except Exception as exc:
            logger.warning("9mobile sync failed: %s", exc)

    # Refresh plans after sync
    plans = db.query(DataPlan).filter(DataPlan.is_active == True).all()
    
    # Final breakdown log
    breakdown = {}
    for p in plans:
        nw = str(p.network or "unknown").lower()
        breakdown[nw] = breakdown.get(nw, 0) + 1
    logger.info("Final plans breakdown: %s", breakdown)

    logger.info("Final active plans breakdown: %s", breakdown)

    # The Admin dashboard's 'is_active' flag is now the absolute source of truth.
    # No more hardcoded filters or provider-specific exclusions.
    # If a plan is active in the DB, it is displayed.

    promo_snapshot = _mtn_1gb_promo_snapshot(db)
    user_promo_used = _user_has_used_mtn_1gb_promo(db, user.id)
    priced = []
    for plan in plans:
        price = get_price_for_user(db, plan, user.role)
        promo_active = False
        promo_old_price = None
        promo_label = None
        promo_remaining = None
        promo_limit = None
        if _is_mtn_1gb_promo_plan(plan):
            promo_limit = int(promo_snapshot["limit"])
            promo_remaining = int(promo_snapshot["remaining"])
            promo_active = bool(promo_snapshot["active"]) and not user_promo_used
            if promo_active:
                promo_price = Decimal(str(promo_snapshot["price"]))
                if promo_price < Decimal(str(price)):
                    promo_old_price = Decimal(str(price))
                    price = promo_price
                    promo_label = "First 50 promo"
        priced.append(
            DataPlanOut(
                id=plan.id,
                network=plan.network,
                plan_code=plan.plan_code,
                plan_name=_clean_plan_label(plan.plan_name),
                data_size=plan.data_size,
                validity=plan.validity,
                price=price,
                base_price=plan.base_price,
                promo_active=promo_active,
                promo_old_price=promo_old_price,
                promo_label=promo_label,
                promo_remaining=promo_remaining,
                promo_limit=promo_limit,
                provider=plan.provider,
                provider_plan_id=plan.provider_plan_id,
            )
        )
    priced.sort(key=lambda p: (str(p.network or "").lower(), p.price or 0, _parse_size_gb(p.data_size or p.plan_name) or 0))
    return priced


@router.post("/purchase")
@limiter.limit("5/minute")
def buy_data(request: Request, payload: BuyDataRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    plan_code_input = str(payload.plan_code or "").strip()
    payload_network = str(payload.network or "").strip().lower()
    plan_query = db.query(DataPlan).filter(DataPlan.plan_code == plan_code_input, DataPlan.is_active == True)
    if payload_network:
        plan_query = plan_query.filter(func.lower(DataPlan.network) == payload_network)
    plan = plan_query.first()
    if not plan and ":" not in plan_code_input and plan_code_input:
        suffix_query = (
            db.query(DataPlan)
            .filter(DataPlan.plan_code.like(f"%:{plan_code_input}"), DataPlan.is_active == True)
        )
        if payload_network:
            suffix_query = suffix_query.filter(func.lower(DataPlan.network) == payload_network)
        suffix_matches = suffix_query.all()
        if len(suffix_matches) == 1:
            plan = suffix_matches[0]
        elif len(suffix_matches) > 1:
            raise HTTPException(status_code=400, detail="Plan code is ambiguous. Refresh plans and retry.")
    
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    price = get_price_for_user(db, plan, user.role)
    if _is_mtn_1gb_promo_plan(plan) and not _user_has_used_mtn_1gb_promo(db, user.id):
        promo_snapshot = _mtn_1gb_promo_snapshot(db)
        if bool(promo_snapshot["active"]):
            promo_price = Decimal(str(promo_snapshot["price"]))
            if promo_price < Decimal(str(price)):
                price = promo_price

    wallet = get_or_create_wallet(db, user.id)
    enforce_purchase_limits(db, user_id=user.id, amount=Decimal(price), tx_type=TransactionType.DATA.value)
    if Decimal(wallet.balance) < Decimal(price):
        raise HTTPException(status_code=400, detail="Insufficient balance")

    reference = f"DATA_{secrets.token_hex(8)}"
    transaction = Transaction(
        user_id=user.id,
        reference=reference,
        network=plan.network,
        recipient_phone=str(payload.phone_number or "").strip(),
        data_plan_code=plan.plan_code,
        amount=price,
        status=TransactionStatus.PENDING,
        tx_type=TransactionType.DATA,
    )
    db.add(transaction)
    db.commit()
    db.refresh(transaction)

    debit_wallet(db, wallet, Decimal(price), reference, f"Data purchase to {payload.phone_number}")

    network_key = str(plan.network or "").strip().lower()
    
    # --- STRICT PROVIDER PARTITIONING ---
    
    # 1. Airtel -> SMEPlug
    if network_key == "airtel":
        smeplug = SMEPlugProvider()
        start = time.time()
        try:
            _, suffix = split_plan_code(plan.plan_code)
            provider_plan_id = plan.provider_plan_id or suffix
            result = smeplug.purchase_airtel_data(phone=str(payload.phone_number).strip(), plan_id=provider_plan_id, client_request_id=reference)
            
            db.add(ApiLog(user_id=user.id, service="smeplug", endpoint="/data/purchase", status_code=200, duration_ms=round((time.time() - start) * 1000, 2), reference=reference, success=1 if result.get("status") == "success" else 0))
            
            transaction.provider = "smeplug"
            transaction.external_reference = result.get("provider_reference")
            if result.get("status") == "success":
                transaction.status = TransactionStatus.SUCCESS
            elif result.get("status") == "failed":
                transaction.status = TransactionStatus.FAILED
                credit_wallet(db, wallet, Decimal(price), reference, f"Refund: {result.get('error') or 'Airtel purchase failed'}")
                transaction.status = TransactionStatus.REFUNDED
            db.commit()
            return {"reference": reference, "status": transaction.status, "message": result.get("error") or "Processed", "provider": "smeplug"}
        except Exception as exc:
            transaction.status = TransactionStatus.PENDING
            db.commit()
            return {"reference": reference, "status": "pending", "message": "Pending confirmation"}

    # 2. MTN & Glo -> Amigo
    elif network_key in {"mtn", "glo"}:
        client = AmigoClient()
        start = time.time()
        try:
            amigo_network_id = 1 if network_key == "mtn" else 3
            _, suffix = split_plan_code(plan.plan_code)
            provider_plan_id = plan.provider_plan_id or suffix
            amigo_payload = {"network": amigo_network_id, "plan": provider_plan_id, "mobile_number": str(payload.phone_number).strip()}
            result = client.purchase_data(amigo_payload, idempotency_key=reference)
            
            db.add(ApiLog(user_id=user.id, service="amigo", endpoint="/data/", status_code=200, duration_ms=round((time.time() - start) * 1000, 2), reference=reference, success=1))
            
            transaction.provider = "amigo"
            transaction.status = TransactionStatus.SUCCESS
            transaction.external_reference = str(result.get("orderid") or result.get("reference") or "")
            db.commit()
            return {"reference": reference, "status": "success", "message": "Purchase successful", "provider": "amigo"}
        except Exception as exc:
            transaction.status = TransactionStatus.FAILED
            credit_wallet(db, wallet, Decimal(price), reference, f"Refund: {str(exc)}")
            transaction.status = TransactionStatus.REFUNDED
            db.commit()
            return {"reference": reference, "status": "refunded", "message": str(exc), "provider": "amigo"}

    # 3. 9mobile -> ClubKonnect
    else:
        provider = get_bills_provider()
        start = time.time()
        try:
            _, suffix = split_plan_code(plan.plan_code)
            provider_plan_id = plan.provider_plan_id or suffix
            result = provider.purchase_data(network=network_key, phone_number=str(payload.phone_number).strip(), plan_code=provider_plan_id, request_id=reference)
            
            db.add(ApiLog(user_id=user.id, service="clubkonnect", endpoint="/APIDatabundleV1.asp", status_code=200 if result.success else 400, duration_ms=round((time.time() - start) * 1000, 2), reference=reference, success=1 if result.success else 0))
            
            transaction.provider = "clubkonnect"
            transaction.external_reference = result.external_reference
            if result.success:
                transaction.status = TransactionStatus.SUCCESS
            else:
                transaction.status = TransactionStatus.FAILED
                credit_wallet(db, wallet, Decimal(price), reference, f"Refund: {result.message}")
                transaction.status = TransactionStatus.REFUNDED
            db.commit()
            return {"reference": reference, "status": transaction.status, "message": result.message or "Processed"}
        except Exception as exc:
            transaction.status = TransactionStatus.PENDING
            db.commit()
            return {"reference": reference, "status": "pending", "message": "Pending confirmation"}


@router.post("/sync")
def sync_data_plans(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    # Sync MTN/Glo from Amigo
    amigo_updated = 0
    try:
        client = AmigoClient()
        response = client.fetch_data_plans()
        for item in response.get("data", []):
            if str(item.get("network")).lower() in {"mtn", "glo"}:
                item["provider"] = "amigo"
                amigo_updated += 1 if _upsert_plan_from_provider(db, item) else 0
        db.commit()
    except Exception as exc:
        logger.warning("Amigo sync failed: %s", exc)

    # Sync Airtel from SMEPlug
    smeplug_updated = 0
    try:
        smeplug = SMEPlugProvider()
        for item in smeplug.get_airtel_plans():
            item["provider"] = "smeplug"
            smeplug_updated += 1 if _upsert_plan_from_provider(db, item) else 0
        db.commit()
    except Exception as exc:
        logger.warning("SMEPlug sync failed: %s", exc)

    # Sync 9mobile from ClubKonnect
    ck_updated = 0
    try:
        provider = get_bills_provider()
        if hasattr(provider, "fetch_data_variations"):
            for item in provider.fetch_data_variations("9mobile"):
                item["network"] = "9mobile"
                item["provider"] = "clubkonnect"
                ck_updated += 1 if _upsert_plan_from_provider(db, item) else 0
        db.commit()
    except Exception as exc:
        logger.warning("ClubKonnect sync failed: %s", exc)

    return {"updated": amigo_updated + smeplug_updated + ck_updated, "details": {"amigo": amigo_updated, "smeplug": smeplug_updated, "clubkonnect": ck_updated}}
