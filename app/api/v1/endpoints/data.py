import hashlib
import logging
import re
import secrets
import time
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
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
from app.services.bills import get_bills_provider
from app.services.fraud import enforce_purchase_limits
from app.services.wallet import get_or_create_wallet, debit_wallet, credit_wallet
from app.services.pricing import get_price_for_user
from app.middlewares.rate_limit import limiter
from app.utils.cache import get_cached, set_cached

router = APIRouter()
settings = get_settings()
logger = logging.getLogger(__name__)

_PLAN_CACHE_VERSION = "v4"


_SUCCESS_STATUS = {"success", "successful", "delivered", "completed", "ok", "done"}
_PENDING_STATUS = {"pending", "processing", "queued", "in_progress", "accepted", "submitted"}
_FAILURE_STATUS = {"failed", "fail", "error", "rejected", "declined", "cancelled", "canceled", "refunded"}

_SUCCESS_HINTS = ("successfully", "delivered", "gifted", "completed")
_FAILURE_HINTS = ("failed", "unsuccessful", "unable", "error", "rejected", "declined", "cancelled", "canceled")
_PENDING_HINTS = ("pending", "processing", "queued", "in progress", "submitted")
_VTPASS_DATA_NETWORKS = {"9mobile", "etisalat", "t2"}
_AMIGO_DATA_NETWORKS = {"mtn", "glo", "airtel"}
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

_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(GB|MB)", re.IGNORECASE)
_VALIDITY_RE = re.compile(r"(\d+)\s*(d|day|days|month|months|week|weeks)", re.IGNORECASE)


def _safe_reason(value: str, limit: int = 255) -> str:
    text = str(value or "").strip()
    return text[:limit] if text else "Unknown provider error"


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


def _parse_validity_days(value: str | None) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = _VALIDITY_RE.search(text)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    if unit.startswith("month"):
        return amount * 30
    if unit.startswith("week"):
        return amount * 7
    return amount


def _plan_price_value(plan: DataPlanOut) -> Decimal:
    try:
        return Decimal(str(plan.price or 0))
    except Exception:
        return Decimal("0")


def _closest_target_size(size_gb: float | None) -> float | None:
    if size_gb is None:
        return None
    best = None
    best_delta = None
    for target in _CURATED_SIZE_TARGETS_GB:
        delta = abs(size_gb - target)
        if best is None or delta < best_delta:
            best = target
            best_delta = delta
    if best is None:
        return None
    # Keep only reasonably close buckets to avoid weird mismatches.
    allowed = max(0.12, best * 0.28)
    if float(best_delta or 0.0) > allowed:
        return None
    return best


def _plan_quality_score(plan: DataPlanOut) -> int:
    score = 0
    combined = " ".join(
        [
            str(plan.plan_name or "").lower(),
            str(plan.data_size or "").lower(),
            str(plan.validity or "").lower(),
        ]
    )
    size_gb = _parse_size_gb(plan.data_size or plan.plan_name)
    validity_days = _parse_validity_days(plan.validity or plan.plan_name)
    target = _closest_target_size(size_gb)

    if target is not None:
        score += 5
    elif size_gb is not None:
        score += 2

    if validity_days is not None:
        if 25 <= validity_days <= 35:
            score += 4
        elif 6 <= validity_days <= 10:
            score += 1
        elif validity_days < 4:
            score -= 2

    if size_gb is not None and 0.15 <= size_gb <= 25:
        score += 1
    if size_gb is not None and size_gb > 40:
        score -= 2

    if any(keyword in combined for keyword in _CURATED_FILTER_KEYWORDS):
        score -= 5

    return score


def _is_airtel_30_day_bundle(plan: DataPlanOut) -> bool:
    if str(plan.network or "").strip().lower() != "airtel":
        return True
    capacity = _extract_capacity(f"{plan.data_size or ''} {plan.plan_name or ''}")
    if capacity not in _AIRTEL_BUNDLE_ORDER:
        return False
    validity_days = _parse_validity_days(str(plan.validity or "") or str(plan.plan_name or ""))
    return validity_days == _AIRTEL_VALIDITY_TARGETS.get(capacity)


def _is_noisy_plan(plan: DataPlanOut) -> bool:
    combined = " ".join(
        [
            str(plan.plan_name or "").lower(),
            str(plan.data_size or "").lower(),
            str(plan.validity or "").lower(),
        ]
    )
    if any(keyword in combined for keyword in _CURATED_FILTER_KEYWORDS):
        return True
    return False


def _curate_sharp_plans(priced: list[DataPlanOut]) -> list[DataPlanOut]:
    """
    Returns all active plans, sorted by network and then by price/size.
    No longer drops 'noisy' plans or limits results, as the Admin's 
    is_active flag is now the absolute source of truth.
    """
    if not priced:
        return []

    # Sort primarily by network, then price, then extracted size
    return sorted(
        priced,
        key=lambda p: (
            str(p.network or "").lower(),
            _plan_price_value(p),
            _parse_size_gb(p.data_size or p.plan_name) or 0.0,
            str(p.plan_name or "")
        )
    )


def _extract_variation_code(variation: dict) -> str:
    # Prefer explicit provider plan-code fields before generic `id`.
    # ClubKonnect frequently sends both `id` and `DataPlan`; `DataPlan` is the
    # one accepted by purchase endpoints.
    preferred_keys = (
        "DataPlan",
        "dataplan",
        "DataPlanID",
        "dataplanid",
        "plan_id",
        "planid",
        "PLANID",
        "data_plan",
        "DATA_PLAN",
        "databundle_id",
        "variation_code",
        "code",
        "variation_id",
        "id",
    )
    for key in preferred_keys:
        value = str(variation.get(key) or "").strip()
        if value:
            return value
    return ""


def _provider_variation_to_item(network: str, variation: dict) -> dict | None:
    code = _extract_variation_code(variation)
    if not code:
        return None
    name = str(
        variation.get("name")
        or variation.get("Name")
        or variation.get("variation_name")
        or variation.get("variation")
        or variation.get("service")
        or variation.get("PRODUCT_NAME")
        or variation.get("plan_name")
        or variation.get("PLANNAME")
        or variation.get("plan")
        or variation.get("DataType")
        or ""
    ).strip()
    price_raw = (
        variation.get("variation_amount")
        or variation.get("variation_price")
        or variation.get("amount")
        or variation.get("price")
        or variation.get("Amount")
        or variation.get("PRODUCT_AMOUNT")
        or variation.get("plan_amount")
        or variation.get("userprice")
        or variation.get("USER_PRICE")
        or variation.get("cost")
        or variation.get("Cost")
        or variation.get("amountcharged")
        or variation.get("AmountCharged")
    )
    if price_raw in (None, ""):
        return None
    try:
        price = Decimal(str(price_raw))
    except Exception:
        return None
    size = (
        str(
            variation.get("data")
            or variation.get("data_size")
            or variation.get("bundle")
            or variation.get("size")
            or variation.get("plan_size")
            or variation.get("bundle_size")
            or variation.get("Data")
            or variation.get("datavalue")
            or variation.get("DataValue")
            or ""
        ).strip()
        or str(variation.get("DataLimit") or variation.get("datalimit") or variation.get("datacapacity") or "").strip()
        or _extract_capacity(name)
    )
    validity = (
        str(
            variation.get("validity")
            or variation.get("duration")
            or variation.get("Validity")
            or variation.get("validity_days")
            or variation.get("days")
            or variation.get("durationdays")
            or ""
        ).strip()
        or _extract_validity(name)
    )
    if validity and validity.isdigit():
        validity = f"{validity}d"
    if not validity:
        validity = "30d"
    plan_name = _clean_plan_label(name or f"{network.upper()} {size or code}")
    return {
        "network": network,
        "plan_code": code,
        "plan_name": plan_name,
        "data_size": size or plan_name,
        "validity": validity,
        "price": price,
        "_source_name": name,
    }


def _retain_airtel_bundle(item: dict) -> bool:
    capacity = _extract_capacity(f"{item.get('data_size') or ''} {item.get('plan_name') or ''}")
    if capacity not in _AIRTEL_BUNDLE_ORDER:
        return False
    validity_days = _parse_validity_days(str(item.get("validity") or "") or str(item.get("_source_name") or ""))
    return validity_days == _AIRTEL_VALIDITY_TARGETS.get(capacity)


def _airtel_bundle_preference(item: dict) -> tuple[int, int, int, Decimal, str]:
    capacity = _extract_capacity(f"{item.get('data_size') or ''} {item.get('plan_name') or ''}")
    order = _AIRTEL_BUNDLE_ORDER.get(capacity, 999)
    target_days = _AIRTEL_VALIDITY_TARGETS.get(capacity, 30)
    validity_days = _parse_validity_days(
        str(item.get("validity") or "") or str(item.get("_source_name") or "")
    )
    if validity_days is None:
        validity_days = 999
    validity_delta = abs(validity_days - target_days)
    price = Decimal(str(item.get("price", 0)))
    code = str(item.get("plan_code") or "")
    source_text = " ".join(
        [
            str(item.get("_source_name") or ""),
            str(item.get("plan_name") or ""),
            str(item.get("data_size") or ""),
        ]
    ).lower()
    direct_rank = 0 if "direct data" in source_text else 1
    return order, validity_delta, direct_rank, price, code

def _plan_display_sort_key(plan: DataPlanOut) -> tuple[int, Decimal, float, str]:
    network = str(plan.network or "").strip().lower()
    if network == "airtel":
        capacity = _extract_capacity(f"{plan.data_size or ''} {plan.plan_name or ''}")
        order = _AIRTEL_BUNDLE_ORDER.get(capacity, 999)
        return order, _plan_price_value(plan), _parse_size_gb(plan.data_size or plan.plan_name) or 0.0, str(plan.plan_name or "")
    return 999, _plan_price_value(plan), _parse_size_gb(plan.data_size or plan.plan_name) or 0.0, str(plan.plan_name or "")


def _normalize_provider_text(value) -> str:
    return str(value or "").strip().lower()


def _provider_bool(value) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
        return None
    raw = _normalize_provider_text(value)
    if raw in {"true", "1", "yes", "ok", "success", "successful", "delivered"}:
        return True
    if raw in {"false", "0", "no", "failed", "fail", "error", "unsuccessful"}:
        return False
    return None


def _bills_pending_status(meta: dict | None) -> str:
    payload = meta or {}
    for provider_key in ("vtpass", "clubkonnect"):
        status = str((payload.get(provider_key) or {}).get("status") or "").strip().lower()
        if status:
            return status
    return ""


def _contains_any(text: str, hints: tuple[str, ...]) -> bool:
    return any(hint in text for hint in hints)


def _classify_provider_outcome(response: dict) -> tuple[TransactionStatus, str]:
    status_text = _normalize_provider_text(
        response.get("status") or response.get("delivery_status") or response.get("state")
    )
    message_text = _normalize_provider_text(response.get("message") or response.get("detail") or "")
    error_text = _normalize_provider_text(response.get("error") or response.get("errors") or "")
    success_flag = _provider_bool(response.get("success"))

    success_signal = (
        success_flag is True
        or status_text in _SUCCESS_STATUS
        or _contains_any(message_text, _SUCCESS_HINTS)
    )
    failure_signal = (
        success_flag is False
        or status_text in _FAILURE_STATUS
        or bool(error_text)
        or _contains_any(message_text, _FAILURE_HINTS)
    )
    pending_signal = status_text in _PENDING_STATUS or _contains_any(message_text, _PENDING_HINTS)

    if success_signal and not failure_signal:
        return TransactionStatus.SUCCESS, ""
    if failure_signal and not success_signal:
        reason = (
            response.get("message")
            or response.get("detail")
            or response.get("error")
            or response.get("errors")
            or "Data provider rejected purchase"
        )
        return TransactionStatus.FAILED, _safe_reason(str(reason))
    if pending_signal or (success_signal and failure_signal):
        return TransactionStatus.PENDING, ""
    return TransactionStatus.PENDING, ""


def _upsert_plan_from_provider(db: Session, item: dict) -> bool:
    network = str(item.get("network") or "").strip().lower()
    if not network:
        return False

    incoming_code = str(item.get("plan_code") or "").strip()
    if not incoming_code:
        return False

    canonical_code = canonical_plan_code(network, incoming_code)
    if not canonical_code:
        return False

    _, provider_code = split_plan_code(canonical_code)
    plan = (
        db.query(DataPlan)
        .filter(DataPlan.plan_code == canonical_code)
        .first()
    )
    if not plan and provider_code:
        # Migrate legacy records that used plain numeric codes.
        plan = (
            db.query(DataPlan)
            .filter(DataPlan.network == network, DataPlan.plan_code == provider_code)
            .first()
        )
        if plan:
            plan.plan_code = canonical_code

    if not plan:
        plan = DataPlan(
            network=network,
            plan_code=canonical_code,
            plan_name=_clean_plan_label(str(item.get("plan_name") or "").strip()) or f"{network.upper()} {provider_code}",
            data_size=str(item.get("data_size") or "").strip() or "—",
            validity=str(item.get("validity") or "").strip(),
            base_price=Decimal(str(item.get("price", 0))),
            is_active=True,
        )
        db.add(plan)
        return True

    plan.network = network
    plan.plan_name = _clean_plan_label(str(item.get("plan_name") or plan.plan_name).strip()) or plan.plan_name
    plan.data_size = str(item.get("data_size") or plan.data_size).strip() or plan.data_size
    plan.validity = str(item.get("validity") or plan.validity).strip() or plan.validity
    plan.base_price = Decimal(str(item.get("price", plan.base_price)))
    plan.is_active = True
    # NOTE: display_price is intentionally NOT overwritten here.
    # Admin-set price overrides are preserved across provider syncs.
    return True



def _invalidate_plans_cache() -> None:
    for role in (UserRole.USER.value, UserRole.RESELLER.value, UserRole.ADMIN.value):
        set_cached(f"plans:{_PLAN_CACHE_VERSION}:{role}", None, ttl_seconds=1)


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


def _normalize_match_token(value: str | None) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def _pick_replacement_plan(
    db: Session,
    original: DataPlan,
    candidate_codes: set[str] | None = None,
) -> DataPlan | None:
    query = db.query(DataPlan).filter(
        func.lower(DataPlan.network) == str(original.network or "").strip().lower(),
        DataPlan.is_active == True,
    )
    candidates = query.all()
    if candidate_codes:
        candidates = [row for row in candidates if str(row.plan_code or "").strip() in candidate_codes]
    if not candidates:
        return None

    wanted_size = _normalize_match_token(original.data_size)
    wanted_validity = _normalize_match_token(original.validity)
    wanted_price = Decimal(str(original.base_price or 0))

    best = None
    best_score = None
    for row in candidates:
        score = 0
        if _normalize_match_token(row.data_size) == wanted_size and wanted_size:
            score += 6
        if _normalize_match_token(row.validity) == wanted_validity and wanted_validity:
            score += 3
        price_delta = abs(Decimal(str(row.base_price or 0)) - wanted_price)
        if price_delta <= Decimal("1"):
            score += 2
        elif price_delta <= Decimal("5"):
            score += 1

        candidate_key = (score, -float(price_delta), -int(row.id or 0))
        if best is None or candidate_key > best_score:
            best = row
            best_score = candidate_key
    return best


@router.get("/plans", response_model=list[DataPlanOut])

def list_data_plans(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    cache_key = f"plans:{_PLAN_CACHE_VERSION}:{user.role.value}"
    cached = get_cached(cache_key)
    if cached:
        return cached

    plans = db.query(DataPlan).filter(DataPlan.is_active == True).all()
    should_sync = not plans
    if should_sync:
        # Auto-seed/refresh from provider when DB is empty.
        client = AmigoClient()
        response = client.fetch_data_plans()
        items = response.get("data", [])
        touched = 0
        for item in items:
            touched += 1 if _upsert_plan_from_provider(db, item) else 0
        if touched:
            db.commit()
            plans = db.query(DataPlan).filter(DataPlan.is_active == True).all()

    # Pull 9mobile plans from configured non-Amigo provider and keep them in sync.
    touched = 0
    network_providers = {
        "9mobile": get_bills_provider(),
    }
    for network, provider in network_providers.items():
        if not hasattr(provider, "fetch_data_variations"):
            continue
        try:
            updated, _ = _refresh_provider_network_plans(db, provider, network)
            touched += updated
        except Exception as exc:
            logger.warning("Provider variations fetch failed for %s: %s", network, exc)
    if touched:
        db.commit()
        _invalidate_plans_cache()
        plans = db.query(DataPlan).filter(DataPlan.is_active == True).all()
    priced = []
    for plan in plans:
        price = get_price_for_user(db, plan, user.role)
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
            )
        )
    curated = _curate_sharp_plans(priced)
    # Keep plans warm longer to reduce repeated DB/provider work on frequent page refreshes.
    set_cached(cache_key, curated, ttl_seconds=120)
    return curated


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
            raise HTTPException(
                status_code=400,
                detail="Plan code is ambiguous across multiple networks. Refresh plans and retry.",
            )
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    price = get_price_for_user(db, plan, user.role)
    wallet = get_or_create_wallet(db, user.id)
    enforce_purchase_limits(db, user_id=user.id, amount=Decimal(price), tx_type=TransactionType.DATA.value)
    if Decimal(wallet.balance) < Decimal(price):
        raise HTTPException(status_code=400, detail="Insufficient balance")

    reference = _client_request_reference("DATA", user.id, getattr(payload, "client_request_id", None)) or f"DATA_{secrets.token_hex(8)}"
    existing = db.query(Transaction).filter(Transaction.user_id == user.id, Transaction.reference == reference).first()
    if existing:
        return {
            "reference": existing.reference,
            "status": existing.status,
            "message": existing.failure_reason or "",
            "provider": "axisvtu",
            "network": existing.network,
            "plan_code": existing.data_plan_code,
            "failure_reason": existing.failure_reason or "",
            "test_mode": False,
        }
    transaction = Transaction(
        user_id=user.id,
        reference=reference,
        network=plan.network,
        data_plan_code=plan.plan_code,
        amount=price,
        status=TransactionStatus.PENDING,
        tx_type=TransactionType.DATA,
    )
    db.add(transaction)
    db.commit()
    db.refresh(transaction)

    debit_wallet(
        db,
        wallet,
        Decimal(price),
        reference,
        f"Data purchase to {str(payload.phone_number or '').strip()}",
    )

    network_key = str(plan.network or "").strip().lower()
    use_bills_provider = not _is_amigo_data_network(network_key)

    if use_bills_provider:
        provider = get_bills_provider()
        if not hasattr(provider, "purchase_data"):
            transaction.status = TransactionStatus.FAILED
            transaction.failure_reason = "No non-Amigo data provider configured for this network."
            credit_wallet(db, wallet, Decimal(price), reference, "Auto refund for unsupported network provider")
            transaction.status = TransactionStatus.REFUNDED
            db.commit()
            raise HTTPException(status_code=502, detail="No data provider configured for this network. Wallet refunded.")

        start = time.time()
        try:
            selected_plan = plan
            _, provider_code = split_plan_code(selected_plan.plan_code)
            result = provider.purchase_data(
                network=network_key,
                phone_number=str(payload.phone_number),
                plan_code=provider_code or str(selected_plan.plan_code),
                amount=float(price),
                request_id=reference,
            )

            message_text = str(result.message or "").strip().lower()
            if (
                not result.success
                and "invalid_dataplan" in message_text
                and hasattr(provider, "fetch_data_variations")
            ):
                try:
                    updated, refreshed_codes = _refresh_provider_network_plans(db, provider, network_key)
                except Exception as sync_exc:
                    logger.warning("Data plan resync failed for %s: %s", network_key, sync_exc)
                    updated, refreshed_codes = 0, set()

                if updated:
                    db.commit()
                    _invalidate_plans_cache()
                retry_plan = _pick_replacement_plan(db, selected_plan, refreshed_codes)
                if retry_plan and str(retry_plan.plan_code or "").strip() != str(selected_plan.plan_code or "").strip():
                    selected_plan = retry_plan
                    transaction.data_plan_code = retry_plan.plan_code
                    _, retry_provider_code = split_plan_code(retry_plan.plan_code)
                    result = provider.purchase_data(
                        network=network_key,
                        phone_number=str(payload.phone_number),
                        plan_code=retry_provider_code or str(retry_plan.plan_code),
                        amount=float(price),
                        request_id=f"{reference}-R1",
                    )

            duration_ms = round((time.time() - start) * 1000, 2)
            provider_service = "bills"
            if (result.meta or {}).get("clubkonnect"):
                provider_service = "clubkonnect"
            elif (result.meta or {}).get("vtpass"):
                provider_service = "vtpass"
            db.add(ApiLog(
                user_id=user.id,
                service=provider_service,
                endpoint="/data/purchase",
                status_code=200 if result.success else 400,
                duration_ms=duration_ms,
                reference=reference,
                success=1 if result.success else 0,
            ))

            provider_status = _bills_pending_status(result.meta)
            if provider_status in _PENDING_STATUS:
                transaction.status = TransactionStatus.PENDING
                transaction.failure_reason = None
            elif result.success:
                transaction.status = TransactionStatus.SUCCESS
                transaction.failure_reason = None
            else:
                transaction.status = TransactionStatus.FAILED
                transaction.failure_reason = _safe_reason(result.message or "Provider failed")
                credit_wallet(db, wallet, Decimal(price), reference, "Auto refund for failed data purchase")
                transaction.status = TransactionStatus.REFUNDED

            transaction.external_reference = result.external_reference
            db.commit()
            return {
                "reference": reference,
                "status": transaction.status,
                "message": str(result.message or "").strip(),
                "provider": provider_service,
                "network": plan.network,
                "plan_code": transaction.data_plan_code or plan.plan_code,
                "failure_reason": str(transaction.failure_reason or "").strip(),
                "test_mode": False,
            }
        except Exception as exc:
            duration_ms = round((time.time() - start) * 1000, 2)
            db.add(ApiLog(
                user_id=user.id,
                service="bills",
                endpoint="/data/purchase",
                status_code=502,
                duration_ms=duration_ms,
                reference=reference,
                success=0,
            ))
            transaction.status = TransactionStatus.FAILED
            transaction.failure_reason = _safe_reason(str(exc))
            credit_wallet(db, wallet, Decimal(price), reference, "Auto refund due to provider error")
            transaction.status = TransactionStatus.REFUNDED
            db.commit()
            raise HTTPException(status_code=502, detail="Data provider temporarily unavailable. Wallet refunded.")

    client = AmigoClient()
    start = time.time()
    try:
        network_id = resolve_network_id(plan.network, plan.plan_code)
        if network_id is None:
            transaction.status = TransactionStatus.FAILED
            transaction.failure_reason = f"Unsupported network: {plan.network}"
            credit_wallet(db, wallet, Decimal(price), reference, "Auto refund for unsupported network")
            transaction.status = TransactionStatus.REFUNDED
            db.commit()
            raise HTTPException(status_code=400, detail="Unsupported network for data purchase")

        response = client.purchase_data(
            {
                "network": network_id,
                "mobile_number": payload.phone_number,
                "plan": normalize_plan_code(plan.plan_code),
                "Ported_number": payload.ported_number,
            },
            idempotency_key=reference,
        )
        duration_ms = round((time.time() - start) * 1000, 2)
        db.add(ApiLog(
            user_id=user.id,
            service="amigo",
            endpoint="/data/purchase",
            status_code=200,
            duration_ms=duration_ms,
            reference=reference,
            success=1,
        ))

        outcome_status, outcome_reason = _classify_provider_outcome(response)
        if outcome_status == TransactionStatus.SUCCESS:
            transaction.status = TransactionStatus.SUCCESS
            transaction.failure_reason = None
            transaction.external_reference = (
                response.get("reference")
                or response.get("transaction_reference")
                or response.get("transaction_id")
            )
        elif outcome_status == TransactionStatus.FAILED:
            transaction.status = TransactionStatus.FAILED
            transaction.failure_reason = outcome_reason or _safe_reason(response.get("message"))
            credit_wallet(db, wallet, Decimal(price), reference, "Auto refund for failed data purchase")
            transaction.status = TransactionStatus.REFUNDED
        else:
            transaction.status = TransactionStatus.PENDING
            transaction.failure_reason = None

        db.commit()
        return {
            "reference": reference,
            "status": transaction.status,
            "message": str(response.get("message") or "").strip(),
            "provider": "amigo",
            "network": plan.network,
            "plan_code": plan.plan_code,
            "failure_reason": str(transaction.failure_reason or "").strip(),
            "test_mode": settings.amigo_test_mode,
        }
    except HTTPException:
        raise
    except AmigoApiError as exc:
        duration_ms = round((time.time() - start) * 1000, 2)
        db.add(ApiLog(
            user_id=user.id,
            service="amigo",
            endpoint="/data/purchase",
            status_code=exc.status_code or 502,
            duration_ms=duration_ms,
            reference=reference,
            success=0,
        ))
        transaction.status = TransactionStatus.FAILED
        transaction.failure_reason = _safe_reason(exc.message)
        credit_wallet(db, wallet, Decimal(price), reference, "Auto refund due to Amigo error")
        transaction.status = TransactionStatus.REFUNDED
        db.commit()
        raise HTTPException(
            status_code=502,
            detail=(
                f"Data provider failed: {_safe_reason(exc.message, 140)}. Wallet refunded. "
                "If this persists, set AMIGO_TEST_MODE=true temporarily."
            ),
        )
    except Exception as exc:
        duration_ms = round((time.time() - start) * 1000, 2)
        db.add(ApiLog(
            user_id=user.id,
            service="amigo",
            endpoint="/data/purchase",
            status_code=500,
            duration_ms=duration_ms,
            reference=reference,
            success=0,
        ))
        transaction.status = TransactionStatus.FAILED
        transaction.failure_reason = _safe_reason(str(exc))
        credit_wallet(db, wallet, Decimal(price), reference, "Auto refund due to Amigo error")
        transaction.status = TransactionStatus.REFUNDED
        db.commit()
        raise HTTPException(status_code=502, detail="Data provider temporarily unavailable. Wallet refunded.")


@router.post("/sync")
def sync_data_plans(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    client = AmigoClient()
    response = client.fetch_data_plans()
    plans = response.get("data", [])
    updated = 0
    for item in plans:
        updated += 1 if _upsert_plan_from_provider(db, item) else 0
    provider = get_bills_provider()
    if hasattr(provider, "fetch_data_variations"):
        for network in ("9mobile",):
            try:
                touched, _ = _refresh_provider_network_plans(db, provider, network)
                updated += touched
            except Exception as exc:
                logger.warning("Provider variations fetch failed for %s: %s", network, exc)
                continue
    db.commit()
    _invalidate_plans_cache()
    return {"updated": updated}
