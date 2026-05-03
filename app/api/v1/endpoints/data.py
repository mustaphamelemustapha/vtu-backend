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
    pending_signal = (
        status_text in _PENDING_STATUS
        or _contains_any(message_text, _PENDING_HINTS)
        or _contains_any(message_text, _PENDING_PROVIDER_HINTS)
        or _contains_any(error_text, _PENDING_PROVIDER_HINTS)
    )

    if success_signal and not failure_signal:
        return TransactionStatus.SUCCESS, ""
    # Provider may return error wrappers like "transaction_failed" while the
    # message still says the request is being processed. Keep those as pending.
    if pending_signal and failure_signal and not success_signal:
        return TransactionStatus.PENDING, ""

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

    # Amigo can acknowledge accepted orders without a final delivery keyword,
    # while still returning a usable provider reference/order id. In production,
    # those transactions are typically delivered shortly after acceptance.
    # We treat this as success to avoid false-pending receipts for users.
    provider_refs = (
        response.get("reference"),
        response.get("transaction_reference"),
        response.get("transaction_id"),
        response.get("orderid"),
        response.get("order_id"),
    )
    status_code_text = _normalize_provider_text(
        response.get("statuscode")
        or response.get("status_code")
        or response.get("StatusCode")
    )
    has_reference = any(str(ref or "").strip() for ref in provider_refs)
    accepted_code = status_code_text in {"100", "200", "201", "202"}
    if (has_reference or accepted_code) and not failure_signal:
        return TransactionStatus.SUCCESS, ""

    return TransactionStatus.PENDING, ""


def _classify_amigo_recheck(response: dict) -> TransactionStatus:
    status, _ = _classify_provider_outcome(response)
    return status


def _is_definitive_amigo_failure(response: dict | None = None, error_message: str | None = None, status_code: int | None = None) -> bool:
    """Return True only for failures that are safely non-delivery (refund-safe)."""
    payload = response or {}
    error_code = _normalize_provider_text(payload.get("error") or payload.get("code") or "")
    message = _normalize_provider_text(error_message or payload.get("message") or payload.get("detail") or "")

    if error_code in _DEFINITIVE_FAILURE_CODES:
        return True
    if _contains_any(message, _DEFINITIVE_FAILURE_HINTS):
        return True
    if status_code is not None and int(status_code) in {401, 403, 404, 422}:
        return True
    return False


def _confirm_pending_amigo_purchase(
    *,
    client: AmigoClient,
    network_id: int,
    phone_number: str,
    plan_code: str,
    idempotency_key: str,
    ported_number: bool,
) -> tuple[TransactionStatus, dict | None, str | None]:
    """
    Best-effort synchronous confirmation for initially pending responses.
    Returns (final_status_guess, last_response, last_message).
    """
    last_response: dict | None = None
    last_message: str | None = None

    for _ in range(_AMIGO_PENDING_CONFIRM_ATTEMPTS):
        time.sleep(_AMIGO_PENDING_CONFIRM_DELAY_SECONDS)
        try:
            response = client.purchase_data(
                {
                    "network": network_id,
                    "mobile_number": phone_number,
                    "plan": normalize_plan_code(plan_code),
                    "Ported_number": ported_number,
                },
                idempotency_key=idempotency_key,
            )
            last_response = response
            outcome_status, outcome_reason = _classify_provider_outcome(response)
            last_message = outcome_reason or str(response.get("message") or "").strip()

            if outcome_status == TransactionStatus.SUCCESS:
                return TransactionStatus.SUCCESS, response, last_message
            if outcome_status == TransactionStatus.FAILED:
                if _is_definitive_amigo_failure(response=response, error_message=last_message):
                    return TransactionStatus.FAILED, response, last_message
                # Ambiguous failure signal: keep waiting for a clear outcome.
                continue
            # Still pending: keep checking within window.
        except AmigoApiError as exc:
            last_message = str(exc.message or "").strip()
            if _is_definitive_amigo_failure(error_message=last_message, status_code=exc.status_code):
                return TransactionStatus.FAILED, last_response, last_message
            continue
        except Exception:
            continue

    return TransactionStatus.PENDING, last_response, last_message


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
    # Note: We NO LONGER force is_active=True for existing plans.
    # This preserves manual Admin toggles during provider syncs.
    # NOTE: display_price is intentionally NOT overwritten here.
    # Admin-set price overrides are preserved across provider syncs.
    return True



def _invalidate_plans_cache() -> None:
    for role in (UserRole.USER.value, UserRole.RESELLER.value, UserRole.ADMIN.value):
        set_cached(f"plans:{_PLAN_CACHE_VERSION}:{role}", None, ttl_seconds=1)


def _enforce_airtel_amigo_catalog(db: Session) -> int:
    """
    Force Airtel plans to the Amigo allowlist only, deactivating legacy rows
    from old providers.
    """
    touched = 0
    rows = db.query(DataPlan).filter(func.lower(DataPlan.network) == "airtel").all()
    for row in rows:
        canonical_code = canonical_plan_code("airtel", str(row.plan_code or "").strip())
        if canonical_code and row.plan_code != canonical_code:
            row.plan_code = canonical_code
            touched += 1
        allowed = canonical_code in _AIRTEL_AMIGO_ALLOWED_CODES
        if not allowed and row.is_active:
            row.is_active = False
            touched += 1
    return touched


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

    airtel_touched = _enforce_airtel_amigo_catalog(db)
    if airtel_touched:
        db.commit()
        _invalidate_plans_cache()
        plans = db.query(DataPlan).filter(DataPlan.is_active == True).all()
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
        recipient_phone=str(payload.phone_number or "").strip(),
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
            failure_msg = outcome_reason or _safe_reason(response.get("message"))
            if _is_definitive_amigo_failure(response=response, error_message=failure_msg):
                transaction.status = TransactionStatus.FAILED
                transaction.failure_reason = failure_msg
                credit_wallet(db, wallet, Decimal(price), reference, "Auto refund for failed data purchase")
                transaction.status = TransactionStatus.REFUNDED
            else:
                # Ambiguous failure signal from provider:
                # keep pending to avoid false refunds on already-delivered bundles.
                transaction.status = TransactionStatus.PENDING
                transaction.failure_reason = _safe_reason(
                    f"Awaiting provider confirmation: {failure_msg}"
                )
        else:
            # Provider accepted but returned pending.
            # Run a short synchronous confirmation window to reduce
            # false-pending receipts for delivered purchases.
            confirmed_status, confirmed_response, confirmed_message = _confirm_pending_amigo_purchase(
                client=client,
                network_id=network_id,
                phone_number=str(payload.phone_number),
                plan_code=plan.plan_code,
                idempotency_key=reference,
                ported_number=payload.ported_number,
            )
            if confirmed_status == TransactionStatus.SUCCESS:
                transaction.status = TransactionStatus.SUCCESS
                transaction.failure_reason = None
                source = confirmed_response or response
                transaction.external_reference = (
                    source.get("reference")
                    or source.get("transaction_reference")
                    or source.get("transaction_id")
                    or transaction.external_reference
                )
            elif confirmed_status == TransactionStatus.FAILED:
                failure_msg = _safe_reason(
                    confirmed_message
                    or outcome_reason
                    or str((confirmed_response or response).get("message") or "Provider rejected transaction")
                )
                if _is_definitive_amigo_failure(response=confirmed_response or response, error_message=failure_msg):
                    transaction.status = TransactionStatus.FAILED
                    transaction.failure_reason = failure_msg
                    credit_wallet(db, wallet, Decimal(price), reference, "Auto refund for failed data purchase")
                    transaction.status = TransactionStatus.REFUNDED
                else:
                    transaction.status = TransactionStatus.FAILED
                    transaction.failure_reason = failure_msg
                    credit_wallet(db, wallet, Decimal(price), reference, "Auto refund after ambiguous confirmation failure")
                    transaction.status = TransactionStatus.REFUNDED
            else:
                # Still unresolved after short synchronous window.
                # Keep pending and let admin/query reconciliation finalize it.
                transaction.status = TransactionStatus.PENDING
                transaction.failure_reason = "Awaiting provider confirmation"

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
        failure_reason = _build_amigo_failure_reason(exc)
        logger.warning(
            "Amigo purchase failed ref=%s status=%s reason=%s raw=%s",
            reference,
            getattr(exc, "status_code", None),
            failure_reason,
            (str(getattr(exc, "raw", "") or "")[:500] or "—"),
        )
        if _is_ambiguous_provider_error(exc):
            # Critical protection: one immediate idempotent recheck before returning pending.
            # This closes the "delivered but pending" window for many ambiguous provider responses.
            try:
                time.sleep(1.0)
                confirm_response = client.purchase_data(
                    {
                        "network": network_id,
                        "mobile_number": payload.phone_number,
                        "plan": normalize_plan_code(plan.plan_code),
                        "Ported_number": payload.ported_number,
                    },
                    idempotency_key=reference,
                )
                confirm_outcome = _classify_amigo_recheck(confirm_response)
                if confirm_outcome == TransactionStatus.SUCCESS:
                    transaction.status = TransactionStatus.SUCCESS
                    transaction.failure_reason = None
                    transaction.external_reference = (
                        confirm_response.get("reference")
                        or confirm_response.get("transaction_reference")
                        or confirm_response.get("transaction_id")
                        or transaction.external_reference
                    )
                    db.commit()
                    return {
                        "reference": reference,
                        "status": transaction.status,
                        "message": str(confirm_response.get("message") or "Transaction confirmed successfully.").strip(),
                        "provider": "amigo",
                        "network": plan.network,
                        "plan_code": plan.plan_code,
                        "failure_reason": "",
                        "test_mode": settings.amigo_test_mode,
                    }
                if confirm_outcome == TransactionStatus.FAILED:
                    confirm_msg = _safe_reason(
                        str(confirm_response.get("message") or "Provider rejected transaction")
                    )
                    if _is_definitive_amigo_failure(response=confirm_response, error_message=confirm_msg):
                        transaction.status = TransactionStatus.FAILED
                        transaction.failure_reason = confirm_msg
                        credit_wallet(db, wallet, Decimal(price), reference, "Auto refund after immediate provider confirmation failure")
                        transaction.status = TransactionStatus.REFUNDED
                        db.commit()
                        raise HTTPException(status_code=502, detail="Data provider rejected this purchase. Wallet refunded.")
                    transaction.status = TransactionStatus.PENDING
                    transaction.failure_reason = _safe_reason(
                        f"Awaiting provider confirmation: {confirm_msg}"
                    )
                    db.commit()
                    return {
                        "reference": reference,
                        "status": transaction.status,
                        "message": "Transaction submitted and awaiting provider confirmation.",
                        "provider": "amigo",
                        "network": plan.network,
                        "plan_code": plan.plan_code,
                        "failure_reason": str(transaction.failure_reason or "").strip(),
                        "test_mode": settings.amigo_test_mode,
                    }
            except HTTPException:
                raise
            except Exception as recheck_exc:
                logger.warning("Immediate data confirmation recheck failed for %s: %s", reference, recheck_exc)

            # Still ambiguous after immediate recheck: keep pending (no auto-refund).
            transaction.status = TransactionStatus.PENDING
            transaction.failure_reason = _safe_reason(
                f"Awaiting provider confirmation: {failure_reason}"
            )
            db.commit()
            return {
                "reference": reference,
                "status": transaction.status,
                "message": "Transaction submitted and awaiting provider confirmation.",
                "provider": "amigo",
                "network": plan.network,
                "plan_code": plan.plan_code,
                "failure_reason": str(transaction.failure_reason or "").strip(),
                "test_mode": settings.amigo_test_mode,
            }

        if _is_definitive_amigo_failure(error_message=exc.message, status_code=exc.status_code):
            transaction.status = TransactionStatus.FAILED
            transaction.failure_reason = failure_reason
            credit_wallet(db, wallet, Decimal(price), reference, "Auto refund due to Amigo error")
            transaction.status = TransactionStatus.REFUNDED
            db.commit()
            raise HTTPException(
                status_code=502,
                detail="Data provider rejected this purchase. Wallet refunded.",
            )

        transaction.status = TransactionStatus.PENDING
        transaction.failure_reason = _safe_reason(
            f"Awaiting provider confirmation: {failure_reason}"
        )
        db.commit()
        return {
            "reference": reference,
            "status": transaction.status,
            "message": "Transaction submitted and awaiting provider confirmation.",
            "provider": "amigo",
            "network": plan.network,
            "plan_code": plan.plan_code,
            "failure_reason": str(transaction.failure_reason or "").strip(),
            "test_mode": settings.amigo_test_mode,
        }
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
    updated += _enforce_airtel_amigo_catalog(db)
    db.commit()
    _invalidate_plans_cache()
    return {"updated": updated}
