from datetime import date, datetime, time, timezone, timedelta
from typing import Optional
from decimal import Decimal
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, or_, select, union_all, String, cast, inspect
from app.core.database import get_db
from app.core.config import get_settings
from app.dependencies import require_admin
from app.models import User, Wallet, WalletLedger, Transaction, ServiceTransaction, TransactionStatus, TransactionType, PricingRule, PricingRole, MarginType, ApiLog, DataPlan, TransactionDispute, DisputeStatus, AdminAuditLog, ServiceToggle, Referral
from app.schemas.admin import (
    FundUserWalletRequest,
    PricingRuleUpdate,
    PricingRulesResponse,
    AdminTransactionsResponse,
    AdminUsersResponse,
    AdminReportOut,
    AdminReportsResponse,
    AdminReportActionRequest,
    AdjustWalletRequest,
    ServiceToggleUpdate,
    ServiceToggleOut,
    DataPlanUpdate,
    AdminDataPlanOut,
    AdminAuditLogsResponse,
    AdminReferralsResponse,
    ReconcileTransactionRequest,
    ReconcileTransactionsBulkRequest,
)
from app.services.wallet import get_or_create_wallet, credit_wallet, debit_wallet
from app.services.pricing import build_service_pricing_key, parse_pricing_key
from app.api.v1.endpoints.data import _invalidate_plans_cache

router = APIRouter()
settings = get_settings()

_SENSITIVE_META_KEYS = {
    "api_key",
    "apikey",
    "token",
    "authorization",
    "x-api-key",
    "secret",
    "password",
}


def _sanitize_meta(value):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            key_text = str(key or "")
            if key_text.strip().lower() in _SENSITIVE_META_KEYS:
                out[key] = "***redacted***"
            else:
                out[key] = _sanitize_meta(item)
        return out
    if isinstance(value, list):
        return [_sanitize_meta(item) for item in value]
    return value


def _extract_provider_payload(meta: dict | None):
    payload = meta or {}
    if not isinstance(payload, dict):
        return None
    for key in ("provider_response", "raw_provider_response", "provider_raw", "response", "result"):
        if key in payload and payload.get(key) is not None:
            return payload.get(key)
    for provider_key in ("amigo", "clubkonnect", "vtpass"):
        provider_obj = payload.get(provider_key)
        if isinstance(provider_obj, dict):
            for key in ("raw_response", "response", "result"):
                if key in provider_obj and provider_obj.get(key) is not None:
                    return provider_obj.get(key)
            if provider_obj:
                return provider_obj
    return payload if payload else None

def _coerce_status(value: Optional[str]) -> Optional[TransactionStatus]:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    for member in TransactionStatus:
        if raw.lower() == member.value.lower() or raw.upper() == member.name:
            return member
    raise HTTPException(status_code=400, detail="Invalid status")


def _coerce_type(value: Optional[str]) -> Optional[TransactionType]:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    for member in TransactionType:
        if raw.lower() == member.value.lower() or raw.upper() == member.name:
            return member
    raise HTTPException(status_code=400, detail="Invalid tx_type")


def _as_utc_start(d: date) -> datetime:
    return datetime.combine(d, time.min).replace(tzinfo=timezone.utc)


def _as_utc_end(d: date) -> datetime:
    return datetime.combine(d, time.max).replace(tzinfo=timezone.utc)


def _normalize_status_value(value) -> str:
    if value is None:
        return ""
    if hasattr(value, "value"):
        return str(value.value).lower()
    raw = str(value).strip()
    if not raw:
        return ""
    for member in TransactionStatus:
        if raw.lower() == member.value.lower() or raw.upper() == member.name:
            return member.value
    return raw.lower()


def _normalize_type_value(value) -> str:
    if value is None:
        return ""
    if hasattr(value, "value"):
        return str(value.value).lower()
    raw = str(value).strip()
    if not raw:
        return ""
    for member in TransactionType:
        if raw.lower() == member.value.lower() or raw.upper() == member.name:
            return member.value
    return raw.lower()


def _safe_reason(message: Optional[str], limit: int = 240) -> str:
    text = str(message or "").strip()
    if not text:
        return "Admin marked transaction as failed and refunded."
    return text[:limit]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@router.get("/analytics")

def analytics(admin=Depends(require_admin), db: Session = Depends(get_db)):
    total_revenue = db.query(func.sum(Transaction.amount)).filter(Transaction.status == TransactionStatus.SUCCESS).scalar() or 0
    data_revenue = (
        db.query(func.sum(Transaction.amount))
        .filter(
            Transaction.status == TransactionStatus.SUCCESS,
            Transaction.tx_type == TransactionType.DATA,
        )
        .scalar()
        or 0
    )
    # Estimate cost from the current plan catalog base_price (not historical).
    data_cost_estimate = (
        db.query(func.sum(DataPlan.base_price))
        .select_from(Transaction)
        .join(DataPlan, Transaction.data_plan_code == DataPlan.plan_code)
        .filter(
            Transaction.status == TransactionStatus.SUCCESS,
            Transaction.tx_type == TransactionType.DATA,
        )
        .scalar()
        or 0
    )
    gross_profit_estimate = data_revenue - data_cost_estimate
    gross_margin_pct = (float(gross_profit_estimate) / float(data_revenue) * 100.0) if float(data_revenue) else 0.0
    total_users = db.query(func.count(User.id)).scalar() or 0
    daily_tx = db.query(func.count(Transaction.id)).filter(Transaction.status == TransactionStatus.SUCCESS).scalar() or 0
    api_success = db.query(func.count(ApiLog.id)).filter(ApiLog.success == 1).scalar() or 0
    api_failed = db.query(func.count(ApiLog.id)).filter(ApiLog.success == 0).scalar() or 0
    service_revenue = 0
    service_cost_estimate = 0
    service_profit_estimate = 0
    reports_open = 0
    reports_resolved = 0
    promo_users_used = 0
    promo_remaining = 0
    promo_limit = max(int(settings.promo_mtn_1gb_limit or 0), 0)
    promo_active = False
    period_starts = {}
    period_profit_estimates = {
        "daily": {"revenue": 0.0, "cost_estimate": 0.0, "profit_estimate": 0.0, "tx_count": 0},
        "weekly": {"revenue": 0.0, "cost_estimate": 0.0, "profit_estimate": 0.0, "tx_count": 0},
        "monthly": {"revenue": 0.0, "cost_estimate": 0.0, "profit_estimate": 0.0, "tx_count": 0},
    }
    now_utc = _utcnow()
    day_start_utc = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start_utc = day_start_utc - timedelta(days=day_start_utc.weekday())
    month_start_utc = day_start_utc.replace(day=1)
    period_starts["daily"] = day_start_utc
    period_starts["weekly"] = week_start_utc
    period_starts["monthly"] = month_start_utc

    def _apply_period_totals(created_at, revenue: float, cost: float):
        created_utc = _ensure_utc(created_at)
        if not created_utc:
            return
        for key, start_at in period_starts.items():
            if created_utc >= start_at:
                target = period_profit_estimates[key]
                target["revenue"] += revenue
                target["cost_estimate"] += cost
                target["profit_estimate"] += revenue - cost
                target["tx_count"] += 1

    try:
        data_period_rows = (
            db.query(Transaction.created_at, Transaction.amount, DataPlan.base_price)
            .outerjoin(DataPlan, Transaction.data_plan_code == DataPlan.plan_code)
            .filter(
                Transaction.status == TransactionStatus.SUCCESS,
                Transaction.created_at >= month_start_utc,
            )
            .all()
        )
        for created_at, amount, base_price in data_period_rows:
            revenue_num = float(amount or 0)
            try:
                cost_num = float(base_price) if base_price is not None else revenue_num
            except Exception:
                cost_num = revenue_num
            _apply_period_totals(created_at, revenue_num, cost_num)

        if inspect(db.bind).has_table("service_transactions"):
            rows = (
                db.query(ServiceTransaction)
                .filter(ServiceTransaction.status == TransactionStatus.SUCCESS.value)
                .all()
            )
            for tx in rows:
                amt = float(tx.amount or 0)
                meta = tx.meta or {}
                base = meta.get("base_amount")
                try:
                    base_num = float(base) if base is not None else amt
                except Exception:
                    base_num = amt
                service_revenue += amt
                service_cost_estimate += base_num
            service_profit_estimate = service_revenue - service_cost_estimate
            service_period_rows = (
                db.query(ServiceTransaction.created_at, ServiceTransaction.amount, ServiceTransaction.meta)
                .filter(
                    ServiceTransaction.status == TransactionStatus.SUCCESS.value,
                    ServiceTransaction.created_at >= month_start_utc,
                )
                .all()
            )
            for created_at, amount, meta in service_period_rows:
                revenue_num = float(amount or 0)
                base = (meta or {}).get("base_amount") if isinstance(meta, dict) else None
                try:
                    cost_num = float(base) if base is not None else revenue_num
                except Exception:
                    cost_num = revenue_num
                _apply_period_totals(created_at, revenue_num, cost_num)
        if inspect(db.bind).has_table("transaction_disputes"):
            reports_open = (
                db.query(func.count(TransactionDispute.id))
                .filter(TransactionDispute.status == DisputeStatus.OPEN)
                .scalar()
                or 0
            )
            reports_resolved = (
                db.query(func.count(TransactionDispute.id))
                .filter(TransactionDispute.status == DisputeStatus.RESOLVED)
                .scalar()
                or 0
            )
        if bool(settings.promo_mtn_1gb_enabled) and promo_limit > 0:
            promo_network = str(settings.promo_mtn_1gb_network or "mtn").strip().lower()
            promo_suffix = str(settings.promo_mtn_1gb_plan_code or "1001").strip().lower()
            promo_users_used = int(
                db.query(func.count(func.distinct(Transaction.user_id)))
                .filter(
                    Transaction.tx_type == TransactionType.DATA,
                    Transaction.status == TransactionStatus.SUCCESS,
                    func.lower(Transaction.network) == promo_network,
                    or_(
                        func.lower(Transaction.data_plan_code) == promo_suffix,
                        func.lower(Transaction.data_plan_code).like(f"%:{promo_suffix}"),
                    ),
                )
                .scalar()
                or 0
            )
            promo_remaining = max(promo_limit - promo_users_used, 0)
            promo_active = promo_remaining > 0
    except Exception:
        # Keep analytics endpoint resilient when the service table is not yet available.
        pass

    period_profit_payload = {}
    for key, values in period_profit_estimates.items():
        period_profit_payload[key] = {
            "revenue": round(float(values["revenue"]), 2),
            "cost_estimate": round(float(values["cost_estimate"]), 2),
            "profit_estimate": round(float(values["profit_estimate"]), 2),
            "tx_count": int(values["tx_count"]),
        }

    return {
        "total_revenue": total_revenue,
        "data_revenue": data_revenue,
        "data_cost_estimate": data_cost_estimate,
        "gross_profit_estimate": gross_profit_estimate,
        "gross_margin_pct": round(gross_margin_pct, 2),
        "service_revenue": round(float(service_revenue), 2),
        "service_cost_estimate": round(float(service_cost_estimate), 2),
        "service_profit_estimate": round(float(service_profit_estimate), 2),
        "total_users": total_users,
        "daily_transactions": daily_tx,
        "api_success": api_success,
        "api_failed": api_failed,
        "reports_open": int(reports_open),
        "reports_resolved": int(reports_resolved),
        "promo_mtn_1gb_enabled": bool(settings.promo_mtn_1gb_enabled),
        "promo_mtn_1gb_limit": int(promo_limit),
        "promo_mtn_1gb_users_used": int(promo_users_used),
        "promo_mtn_1gb_remaining": int(promo_remaining),
        "promo_mtn_1gb_active": bool(promo_active),
        "profit_period_estimates": period_profit_payload,
    }

@router.get("/transactions", response_model=AdminTransactionsResponse)
def list_all_transactions(
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
    q: Optional[str] = None,
    status: Optional[str] = None,
    tx_type: Optional[str] = None,
    network: Optional[str] = None,
    from_date: Optional[date] = Query(default=None, alias="from"),
    to_date: Optional[date] = Query(default=None, alias="to"),
    page: int = 1,
    page_size: int = 50,
):
    if page < 1:
        raise HTTPException(status_code=400, detail="page must be >= 1")
    if page_size < 1 or page_size > 200:
        raise HTTPException(status_code=400, detail="page_size must be between 1 and 200")

    status_enum = _coerce_status(status)
    type_enum = _coerce_type(tx_type)

    # Test stubs (and some lightweight DB wrappers) don't implement Session.execute.
    # Fall back to the ORM query used previously so unit tests keep working.
    if not hasattr(db, "execute"):
        query = (
            db.query(Transaction, User.email.label("user_email"))
            .join(User, Transaction.user_id == User.id)
        )
        if q:
            needle = f"%{q.strip()}%"
            query = query.filter(
                or_(
                    Transaction.reference.ilike(needle),
                    Transaction.external_reference.ilike(needle),
                    Transaction.data_plan_code.ilike(needle),
                    User.email.ilike(needle),
                )
            )
        if status_enum is not None:
            query = query.filter(Transaction.status == status_enum)
        if type_enum is not None:
            query = query.filter(Transaction.tx_type == type_enum)
        if network:
            query = query.filter(Transaction.network == network.strip().lower())
        if from_date:
            query = query.filter(Transaction.created_at >= _as_utc_start(from_date))
        if to_date:
            query = query.filter(Transaction.created_at <= _as_utc_end(to_date))

        total = query.count()
        rows = (
            query.order_by(Transaction.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        items = []
        for tx, user_email in rows:
            items.append(
                {
                    "id": tx.id,
                    "created_at": tx.created_at,
                    "user_id": tx.user_id,
                    "user_email": user_email,
                    "reference": tx.reference,
                    "tx_type": _normalize_type_value(tx.tx_type),
                    "amount": tx.amount,
                    "status": _normalize_status_value(tx.status),
                    "network": tx.network,
                    "data_plan_code": tx.data_plan_code,
                    "external_reference": tx.external_reference,
                    "failure_reason": tx.failure_reason,
                }
            )
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    base_sel = (
        select(
            Transaction.id.label("id"),
            Transaction.created_at.label("created_at"),
            Transaction.user_id.label("user_id"),
            User.email.label("user_email"),
            Transaction.reference.label("reference"),
            cast(Transaction.tx_type, String).label("tx_type"),
            Transaction.amount.label("amount"),
            cast(Transaction.status, String).label("status"),
            Transaction.network.label("network"),
            Transaction.data_plan_code.label("data_plan_code"),
            Transaction.external_reference.label("external_reference"),
            Transaction.failure_reason.label("failure_reason"),
        )
        .select_from(Transaction)
        .join(User, Transaction.user_id == User.id)
    )
    has_services = False
    try:
        has_services = inspect(db.bind).has_table("service_transactions")
    except Exception:
        has_services = False

    if has_services:
        extra_sel = (
            select(
                ServiceTransaction.id.label("id"),
                ServiceTransaction.created_at.label("created_at"),
                ServiceTransaction.user_id.label("user_id"),
                User.email.label("user_email"),
                ServiceTransaction.reference.label("reference"),
                ServiceTransaction.tx_type.label("tx_type"),
                ServiceTransaction.amount.label("amount"),
                ServiceTransaction.status.label("status"),
                ServiceTransaction.provider.label("network"),
                ServiceTransaction.product_code.label("data_plan_code"),
                ServiceTransaction.external_reference.label("external_reference"),
                ServiceTransaction.failure_reason.label("failure_reason"),
            )
            .select_from(ServiceTransaction)
            .join(User, ServiceTransaction.user_id == User.id)
        )
        combined = union_all(base_sel, extra_sel).subquery("all_tx")
    else:
        combined = base_sel.subquery("all_tx")

    where = []
    if q:
        needle = f"%{q.strip()}%"
        where.append(
            or_(
                combined.c.reference.ilike(needle),
                combined.c.external_reference.ilike(needle),
                combined.c.data_plan_code.ilike(needle),
                combined.c.user_email.ilike(needle),
            )
        )
    if status_enum is not None:
        where.append(func.lower(combined.c.status) == status_enum.value.lower())
    if type_enum is not None:
        where.append(func.lower(combined.c.tx_type) == type_enum.value.lower())
    if network:
        where.append(combined.c.network == network.strip().lower())
    if from_date:
        where.append(combined.c.created_at >= _as_utc_start(from_date))
    if to_date:
        where.append(combined.c.created_at <= _as_utc_end(to_date))

    columns = [
        combined.c.id.label("id"),
        combined.c.created_at.label("created_at"),
        combined.c.user_id.label("user_id"),
        combined.c.user_email.label("user_email"),
        combined.c.reference.label("reference"),
        combined.c.tx_type.label("tx_type"),
        combined.c.amount.label("amount"),
        combined.c.status.label("status"),
        combined.c.network.label("network"),
        combined.c.data_plan_code.label("data_plan_code"),
        combined.c.external_reference.label("external_reference"),
        combined.c.failure_reason.label("failure_reason"),
    ]

    total = db.execute(select(func.count()).select_from(combined).where(*where)).scalar() or 0
    rows = (
        db.execute(
            select(*columns)
            .where(*where)
            .order_by(combined.c.created_at.desc(), combined.c.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .mappings()
        .all()
    )

    items = []
    for r in rows:
        row = dict(r)
        row["status"] = _normalize_status_value(row.get("status"))
        row["tx_type"] = _normalize_type_value(row.get("tx_type"))
        items.append(row)

    return {"items": items, "total": int(total), "page": page, "page_size": page_size}


@router.get("/transactions/{reference}")
def get_transaction_details(
    reference: str,
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    ref = (reference or "").strip()
    if not ref:
        raise HTTPException(status_code=400, detail="reference is required")

    tx = db.query(Transaction).filter(Transaction.reference == ref).first()
    if tx:
        raw_meta_value = getattr(tx, "meta", None)
        raw_meta = raw_meta_value if isinstance(raw_meta_value, dict) else {}
        provider_payload = _extract_provider_payload(raw_meta)
        latest_api_log = (
            db.query(ApiLog)
            .filter(ApiLog.reference == tx.reference)
            .order_by(ApiLog.created_at.desc())
            .first()
        )
        provider_trace = {
            "provider": tx.provider or ("amigo" if _normalize_type_value(tx.tx_type) == "data" else "unknown"),
            "tx_status": _normalize_status_value(tx.status),
            "failure_reason": tx.failure_reason or "",
            "external_reference": tx.external_reference or "",
            "api_log_status_code": getattr(latest_api_log, "status_code", None),
            "api_log_duration_ms": (
                float(getattr(latest_api_log, "duration_ms"))
                if getattr(latest_api_log, "duration_ms", None) is not None
                else None
            ),
            "api_log_endpoint": getattr(latest_api_log, "endpoint", None),
            "api_log_service": getattr(latest_api_log, "service", None),
            "api_log_success": getattr(latest_api_log, "success", None),
            "provider_payload_present": provider_payload is not None,
            "meta_present": bool(raw_meta),
        }
        provider_payload_for_display = provider_payload if provider_payload is not None else provider_trace
        return {
            "source": "transaction",
            "id": tx.id,
            "reference": tx.reference,
            "status": _normalize_status_value(tx.status),
            "tx_type": _normalize_type_value(tx.tx_type),
            "amount": tx.amount,
            "user_id": tx.user_id,
            "network": tx.network,
            "data_plan_code": tx.data_plan_code,
            "external_reference": tx.external_reference,
            "failure_reason": tx.failure_reason,
            "created_at": tx.created_at,
            "meta": _sanitize_meta(raw_meta),
            "provider_payload": _sanitize_meta(provider_payload_for_display),
            "provider_payload_pretty": json.dumps(_sanitize_meta(provider_payload_for_display), indent=2, ensure_ascii=False),
            "provider_trace": _sanitize_meta(provider_trace),
        }

    has_services = False
    try:
        has_services = inspect(db.bind).has_table("service_transactions")
    except Exception:
        has_services = False

    if has_services:
        service_tx = db.query(ServiceTransaction).filter(ServiceTransaction.reference == ref).first()
        if service_tx:
            raw_meta = service_tx.meta if isinstance(service_tx.meta, dict) else {}
            provider_payload = _extract_provider_payload(raw_meta)
            return {
                "source": "service_transaction",
                "id": service_tx.id,
                "reference": service_tx.reference,
                "status": _normalize_status_value(service_tx.status),
                "tx_type": _normalize_type_value(service_tx.tx_type),
                "amount": service_tx.amount,
                "user_id": service_tx.user_id,
                "network": service_tx.provider,
                "data_plan_code": service_tx.product_code,
                "external_reference": service_tx.external_reference,
                "failure_reason": service_tx.failure_reason,
                "created_at": service_tx.created_at,
                "meta": _sanitize_meta(raw_meta),
                "provider_payload": _sanitize_meta(provider_payload),
                "provider_payload_pretty": json.dumps(_sanitize_meta(provider_payload), indent=2, ensure_ascii=False)
                if provider_payload is not None
                else None,
            }

    raise HTTPException(status_code=404, detail="Transaction reference not found")


@router.get("/users", response_model=AdminUsersResponse)
def list_users(
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
    q: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
):
    if page < 1:
        raise HTTPException(status_code=400, detail="page must be >= 1")
    if page_size < 1 or page_size > 200:
        raise HTTPException(status_code=400, detail="page_size must be between 1 and 200")

    query = db.query(User)
    if q:
        needle = f"%{q.strip()}%"
        query = query.filter(
            or_(
                User.email.ilike(needle),
                User.full_name.ilike(needle),
                User.phone_number.ilike(needle),
            )
        )

    total = query.count()
    users = (
        query.order_by(User.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for u in users:
        items.append(
            {
                "id": u.id,
                "created_at": u.created_at,
                "email": u.email,
                "phone_number": getattr(u, "phone_number", None),
                "full_name": u.full_name,
                "role": u.role,
                "is_active": u.is_active,
                "is_verified": u.is_verified,
            }
        )

    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/users/{user_id}/details")
def get_user_details(
    user_id: int,
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    wallet = db.query(Wallet).filter(Wallet.user_id == user_id).first()
    recent_items = []

    data_rows = (
        db.query(Transaction)
        .filter(Transaction.user_id == user_id)
        .order_by(Transaction.created_at.desc(), Transaction.id.desc())
        .limit(20)
        .all()
    )
    for tx in data_rows:
        recent_items.append(
            {
                "id": tx.id,
                "created_at": tx.created_at,
                "reference": tx.reference,
                "tx_type": _normalize_type_value(tx.tx_type),
                "status": _normalize_status_value(tx.status),
                "amount": tx.amount,
                "network": tx.network,
                "data_plan_code": tx.data_plan_code,
                "external_reference": tx.external_reference,
                "failure_reason": tx.failure_reason,
            }
        )

    has_services = False
    try:
        has_services = inspect(db.bind).has_table("service_transactions")
    except Exception:
        has_services = False

    if has_services:
        service_rows = (
            db.query(ServiceTransaction)
            .filter(ServiceTransaction.user_id == user_id)
            .order_by(ServiceTransaction.created_at.desc(), ServiceTransaction.id.desc())
            .limit(20)
            .all()
        )
        for tx in service_rows:
            recent_items.append(
                {
                    "id": tx.id,
                    "created_at": tx.created_at,
                    "reference": tx.reference,
                    "tx_type": _normalize_type_value(tx.tx_type),
                    "status": _normalize_status_value(tx.status),
                    "amount": tx.amount,
                    "network": tx.provider,
                    "data_plan_code": tx.product_code,
                    "external_reference": tx.external_reference,
                    "failure_reason": tx.failure_reason,
                }
            )

    floor_utc = datetime.min.replace(tzinfo=timezone.utc)
    recent_items.sort(
        key=lambda item: _ensure_utc(item.get("created_at")) or floor_utc,
        reverse=True,
    )
    recent_items = recent_items[:20]

    return {
        "user": {
            "id": user.id,
            "created_at": user.created_at,
            "email": user.email,
            "phone_number": getattr(user, "phone_number", None),
            "full_name": user.full_name,
            "role": user.role.value if hasattr(user.role, "value") else str(user.role),
            "is_active": user.is_active,
            "is_verified": user.is_verified,
            "referral_code": getattr(user, "referral_code", None),
            "referred_by_id": getattr(user, "referred_by_id", None),
        },
        "wallet": {
            "balance": wallet.balance if wallet else 0,
            "is_locked": wallet.is_locked if wallet else False,
            "updated_at": wallet.updated_at if wallet else None,
        },
        "recent_transactions": recent_items,
    }


@router.get("/wallets")
def list_wallets(
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
    q: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
):
    if page < 1:
        raise HTTPException(status_code=400, detail="page must be >= 1")
    if page_size < 1 or page_size > 200:
        raise HTTPException(status_code=400, detail="page_size must be between 1 and 200")

    query = (
        db.query(User, Wallet)
        .outerjoin(Wallet, Wallet.user_id == User.id)
    )
    if q:
        needle = f"%{q.strip()}%"
        query = query.filter(
            or_(
                User.email.ilike(needle),
                User.full_name.ilike(needle),
                User.phone_number.ilike(needle),
            )
        )

    total = query.count()
    rows = (
        query.order_by(User.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    aggregate_balance = 0.0
    for user, wallet in rows:
        balance = float(wallet.balance) if wallet and wallet.balance is not None else 0.0
        aggregate_balance += balance
        items.append(
            {
                "user_id": user.id,
                "full_name": user.full_name,
                "email": user.email,
                "phone_number": user.phone_number,
                "is_active": user.is_active,
                "wallet_balance": round(balance, 2),
                "wallet_locked": bool(wallet.is_locked) if wallet else False,
                "wallet_updated_at": wallet.updated_at if wallet else None,
            }
        )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "aggregate_balance": round(aggregate_balance, 2),
    }


@router.post("/transactions/reconcile-delivered")
def reconcile_transaction_delivered(
    payload: ReconcileTransactionRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return _reconcile_single_reference(
        db=db,
        admin_email=admin.email,
        reference=(payload.reference or "").strip(),
        note=(payload.note or "").strip() or "Admin marked delivered after customer confirmation.",
    )


@router.post("/transactions/fail-and-refund")
def fail_and_refund_pending_transaction(
    payload: ReconcileTransactionRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return _fail_refund_single_reference(
        db=db,
        admin_email=admin.email,
        reference=(payload.reference or "").strip(),
        note=(payload.note or "").strip() or "Admin marked pending transaction as failed and refunded.",
    )


def _reconcile_single_reference(*, db: Session, admin_email: str, reference: str, note: str):
    reference = str(reference or "").strip()
    if not reference:
        raise HTTPException(status_code=400, detail="reference is required")

    tx = db.query(Transaction).filter(Transaction.reference == reference).first()
    service_tx = None
    source = "transaction"
    if not tx:
        try:
            if inspect(db.bind).has_table("service_transactions"):
                service_tx = db.query(ServiceTransaction).filter(ServiceTransaction.reference == reference).first()
        except Exception:
            service_tx = None
        if not service_tx:
            raise HTTPException(status_code=404, detail="Transaction reference not found")
        source = "service_transaction"

    wallet_action = "none"

    can_write_audit = True
    try:
        can_write_audit = bool(inspect(db.bind).has_table("admin_audit_logs"))
    except Exception:
        can_write_audit = False

    if tx:
        previous_status = _normalize_status_value(tx.status)
        tx.status = TransactionStatus.SUCCESS
        tx.failure_reason = None

        if previous_status == TransactionStatus.REFUNDED.value:
            wallet = get_or_create_wallet(db, tx.user_id)
            reversal_ref = f"REVERSAL_{reference}"
            existing_reversal = (
                db.query(WalletLedger)
                .filter(
                    WalletLedger.wallet_id == wallet.id,
                    WalletLedger.reference == reversal_ref,
                )
                .first()
            )
            if not existing_reversal:
                if Decimal(wallet.balance) < Decimal(tx.amount):
                    raise HTTPException(
                        status_code=409,
                        detail="User wallet balance is lower than refunded amount. Manual recovery required before reconciliation.",
                    )
                debit_wallet(
                    db,
                    wallet,
                    Decimal(tx.amount),
                    reversal_ref,
                    f"Refund reversal for delivered transaction {reference}",
                )
            wallet_action = "refund_reversal_debit"

        if can_write_audit:
            audit_log = AdminAuditLog(
                admin_email=admin_email,
                action="reconcile_delivered",
                target=reference,
                details={
                    "source": source,
                    "previous_status": previous_status,
                    "new_status": TransactionStatus.SUCCESS.value,
                    "wallet_action": wallet_action,
                    "note": note,
                },
            )
            db.add(audit_log)
        db.commit()
        return {
            "status": "ok",
            "reference": reference,
            "source": source,
            "previous_status": previous_status,
            "new_status": TransactionStatus.SUCCESS.value,
            "wallet_action": wallet_action,
        }

    previous_status = _normalize_status_value(service_tx.status)
    service_tx.status = TransactionStatus.SUCCESS.value
    service_tx.failure_reason = None

    if previous_status == TransactionStatus.REFUNDED.value:
        wallet = get_or_create_wallet(db, service_tx.user_id)
        reversal_ref = f"REVERSAL_{reference}"
        existing_reversal = (
            db.query(WalletLedger)
            .filter(
                WalletLedger.wallet_id == wallet.id,
                WalletLedger.reference == reversal_ref,
            )
            .first()
        )
        if not existing_reversal:
            if Decimal(wallet.balance) < Decimal(service_tx.amount):
                raise HTTPException(
                    status_code=409,
                    detail="User wallet balance is lower than refunded amount. Manual recovery required before reconciliation.",
                )
            debit_wallet(
                db,
                wallet,
                Decimal(service_tx.amount),
                reversal_ref,
                f"Refund reversal for delivered transaction {reference}",
            )
        wallet_action = "refund_reversal_debit"

    if can_write_audit:
        audit_log = AdminAuditLog(
            admin_email=admin_email,
            action="reconcile_delivered",
            target=reference,
            details={
                "source": source,
                "previous_status": previous_status,
                "new_status": TransactionStatus.SUCCESS.value,
                "wallet_action": wallet_action,
                "note": note,
            },
        )
        db.add(audit_log)
    db.commit()
    return {
        "status": "ok",
        "reference": reference,
        "source": source,
        "previous_status": previous_status,
        "new_status": TransactionStatus.SUCCESS.value,
        "wallet_action": wallet_action,
    }


def _fail_refund_single_reference(*, db: Session, admin_email: str, reference: str, note: str):
    reference = str(reference or "").strip()
    if not reference:
        raise HTTPException(status_code=400, detail="reference is required")

    tx = db.query(Transaction).filter(Transaction.reference == reference).first()
    service_tx = None
    source = "transaction"
    if not tx:
        try:
            if inspect(db.bind).has_table("service_transactions"):
                service_tx = db.query(ServiceTransaction).filter(ServiceTransaction.reference == reference).first()
        except Exception:
            service_tx = None
        if not service_tx:
            raise HTTPException(status_code=404, detail="Transaction reference not found")
        source = "service_transaction"

    can_write_audit = True
    try:
        can_write_audit = bool(inspect(db.bind).has_table("admin_audit_logs"))
    except Exception:
        can_write_audit = False

    if tx:
        previous_status = _normalize_status_value(tx.status)
        if previous_status != TransactionStatus.PENDING.value:
            raise HTTPException(status_code=409, detail="Only pending transactions can be failed and refunded.")
        wallet = get_or_create_wallet(db, tx.user_id)
        refund_ref = f"ADMIN_REFUND_{reference}"
        if len(refund_ref) > 64:
            refund_ref = refund_ref[:64]
        credit_wallet(
            db,
            wallet,
            Decimal(tx.amount),
            refund_ref,
            f"Admin refund for pending transaction {reference}",
            commit=False,
        )
        tx.status = TransactionStatus.REFUNDED
        tx.failure_reason = _safe_reason(note)
        if can_write_audit:
            db.add(
                AdminAuditLog(
                    admin_email=admin_email,
                    action="fail_refund_pending",
                    target=reference,
                    details={
                        "source": source,
                        "previous_status": previous_status,
                        "new_status": TransactionStatus.REFUNDED.value,
                        "wallet_action": "manual_refund_credit",
                        "refund_reference": refund_ref,
                        "note": note,
                    },
                )
            )
        db.commit()
        return {
            "status": "ok",
            "reference": reference,
            "source": source,
            "previous_status": previous_status,
            "new_status": TransactionStatus.REFUNDED.value,
            "wallet_action": "manual_refund_credit",
        }

    previous_status = _normalize_status_value(service_tx.status)
    if previous_status != TransactionStatus.PENDING.value:
        raise HTTPException(status_code=409, detail="Only pending transactions can be failed and refunded.")
    wallet = get_or_create_wallet(db, service_tx.user_id)
    refund_ref = f"ADMIN_REFUND_{reference}"
    if len(refund_ref) > 64:
        refund_ref = refund_ref[:64]
    credit_wallet(
        db,
        wallet,
        Decimal(service_tx.amount),
        refund_ref,
        f"Admin refund for pending transaction {reference}",
        commit=False,
    )
    service_tx.status = TransactionStatus.REFUNDED.value
    service_tx.failure_reason = _safe_reason(note)
    if can_write_audit:
        db.add(
            AdminAuditLog(
                admin_email=admin_email,
                action="fail_refund_pending",
                target=reference,
                details={
                    "source": source,
                    "previous_status": previous_status,
                    "new_status": TransactionStatus.REFUNDED.value,
                    "wallet_action": "manual_refund_credit",
                    "refund_reference": refund_ref,
                    "note": note,
                },
            )
        )
    db.commit()
    return {
        "status": "ok",
        "reference": reference,
        "source": source,
        "previous_status": previous_status,
        "new_status": TransactionStatus.REFUNDED.value,
        "wallet_action": "manual_refund_credit",
    }


@router.post("/transactions/reconcile-delivered-bulk")
def reconcile_transactions_delivered_bulk(
    payload: ReconcileTransactionsBulkRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    refs: list[str] = []
    seen = set()
    for item in payload.references or []:
        ref = str(item or "").strip()
        if not ref or ref in seen:
            continue
        refs.append(ref)
        seen.add(ref)

    if not refs:
        raise HTTPException(status_code=400, detail="references must contain at least one reference")
    if len(refs) > 200:
        raise HTTPException(status_code=400, detail="maximum 200 references per request")

    note = (payload.note or "").strip() or "Bulk delivered reconciliation after customer confirmation."
    results = []
    ok = 0
    failed = 0
    for ref in refs:
        try:
            item_result = _reconcile_single_reference(
                db=db,
                admin_email=admin.email,
                reference=ref,
                note=note,
            )
            ok += 1
            results.append({"reference": ref, "ok": True, "result": item_result})
        except HTTPException as exc:
            failed += 1
            results.append({"reference": ref, "ok": False, "detail": exc.detail, "status_code": exc.status_code})
        except Exception as exc:
            failed += 1
            results.append({"reference": ref, "ok": False, "detail": str(exc), "status_code": 500})

    return {
        "status": "ok",
        "processed": len(refs),
        "succeeded": ok,
        "failed": failed,
        "results": results,
    }


@router.post("/transactions/fail-and-refund-bulk")
def fail_and_refund_pending_transactions_bulk(
    payload: ReconcileTransactionsBulkRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    refs: list[str] = []
    seen = set()
    for item in payload.references or []:
        ref = str(item or "").strip()
        if not ref or ref in seen:
            continue
        refs.append(ref)
        seen.add(ref)

    if not refs:
        raise HTTPException(status_code=400, detail="references must contain at least one reference")
    if len(refs) > 200:
        raise HTTPException(status_code=400, detail="maximum 200 references per request")

    note = (payload.note or "").strip() or "Bulk admin fail+refund after provider-confirmed failure."
    results = []
    ok = 0
    failed = 0
    for ref in refs:
        try:
            item_result = _fail_refund_single_reference(
                db=db,
                admin_email=admin.email,
                reference=ref,
                note=note,
            )
            ok += 1
            results.append({"reference": ref, "ok": True, "result": item_result})
        except HTTPException as exc:
            failed += 1
            results.append({"reference": ref, "ok": False, "detail": exc.detail, "status_code": exc.status_code})
        except Exception as exc:
            failed += 1
            results.append({"reference": ref, "ok": False, "detail": str(exc), "status_code": 500})

    return {
        "status": "ok",
        "processed": len(refs),
        "succeeded": ok,
        "failed": failed,
        "results": results,
    }


@router.get("/reports", response_model=AdminReportsResponse)
def list_reports(
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
    q: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
):
    if page < 1:
        raise HTTPException(status_code=400, detail="page must be >= 1")
    if page_size < 1 or page_size > 200:
        raise HTTPException(status_code=400, detail="page_size must be between 1 and 200")
    try:
        if not inspect(db.bind).has_table("transaction_disputes"):
            return {"items": [], "total": 0, "page": page, "page_size": page_size}
    except Exception:
        return {"items": [], "total": 0, "page": page, "page_size": page_size}

    query = (
        db.query(TransactionDispute, User.email.label("user_email"))
        .join(User, TransactionDispute.user_id == User.id)
    )
    if q:
        needle = f"%{q.strip()}%"
        query = query.filter(
            or_(
                TransactionDispute.transaction_reference.ilike(needle),
                TransactionDispute.reason.ilike(needle),
                User.email.ilike(needle),
            )
        )
    if status:
        status_raw = status.strip().lower()
        if status_raw in {DisputeStatus.OPEN.value, DisputeStatus.RESOLVED.value, DisputeStatus.REJECTED.value}:
            query = query.filter(TransactionDispute.status == DisputeStatus(status_raw))

    total = query.count()
    rows = (
        query.order_by(TransactionDispute.created_at.desc(), TransactionDispute.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = []
    for report, user_email in rows:
        items.append(
            {
                "id": report.id,
                "created_at": report.created_at,
                "user_id": report.user_id,
                "user_email": user_email,
                "transaction_reference": report.transaction_reference,
                "tx_type": report.tx_type,
                "category": report.category,
                "reason": report.reason,
                "status": report.status.value if hasattr(report.status, "value") else str(report.status),
                "admin_note": report.admin_note,
                "resolved_at": report.resolved_at,
            }
        )

    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.patch("/reports/{report_id}", response_model=AdminReportOut)
def update_report(
    report_id: int,
    payload: AdminReportActionRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        if not inspect(db.bind).has_table("transaction_disputes"):
            raise HTTPException(status_code=503, detail="Reports table is not ready yet")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="Reports table is not ready yet")

    report = db.query(TransactionDispute).filter(TransactionDispute.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    status_raw = (payload.status or "").strip().lower()
    note_raw = (payload.admin_note or "").strip()
    if not status_raw and not note_raw:
        raise HTTPException(status_code=400, detail="Nothing to update")

    if status_raw:
        allowed = {DisputeStatus.OPEN.value, DisputeStatus.RESOLVED.value, DisputeStatus.REJECTED.value}
        if status_raw not in allowed:
            raise HTTPException(status_code=400, detail="Invalid report status")
        next_status = DisputeStatus(status_raw)
        report.status = next_status
        if next_status == DisputeStatus.OPEN:
            report.resolved_at = None
            report.resolved_by = None
        else:
            report.resolved_at = _utcnow()
            report.resolved_by = admin.email

    if payload.admin_note is not None:
        report.admin_note = note_raw or None

    db.commit()
    db.refresh(report)

    user = db.query(User).filter(User.id == report.user_id).first()
    return {
        "id": report.id,
        "created_at": report.created_at,
        "user_id": report.user_id,
        "user_email": user.email if user else "",
        "transaction_reference": report.transaction_reference,
        "tx_type": report.tx_type,
        "category": report.category,
        "reason": report.reason,
        "status": report.status.value if hasattr(report.status, "value") else str(report.status),
        "admin_note": report.admin_note,
        "resolved_at": report.resolved_at,
    }


@router.post("/fund-wallet")

def fund_user_wallet(payload: FundUserWalletRequest, admin=Depends(require_admin), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == payload.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    wallet = get_or_create_wallet(db, user.id)
    
    if payload.amount < 0 and wallet.balance + payload.amount < 0:
        raise HTTPException(status_code=400, detail="Adjustment would result in negative balance")
        
    import uuid
    ref = f"ADMIN_{user.id}_{uuid.uuid4().hex[:8]}"
    credit_wallet(db, wallet, payload.amount, ref, payload.description)
    return {"status": "ok"}


@router.post("/pricing")

def update_pricing(payload: PricingRuleUpdate, admin=Depends(require_admin), db: Session = Depends(get_db)):
    raw_role = (payload.role or "").strip().lower()
    if raw_role not in {"user", "reseller"}:
        raise HTTPException(status_code=400, detail="Invalid role")
    role = PricingRole.USER if raw_role == "user" else PricingRole.RESELLER
    network = (payload.network or "").strip().lower()
    tx_type = (payload.tx_type or "").strip().lower()
    provider = (payload.provider or "").strip().lower()

    if tx_type and tx_type != "data":
        if tx_type not in {"airtime", "cable", "electricity", "exam"}:
            raise HTTPException(status_code=400, detail="Invalid tx_type")
        if not provider and network:
            provider = network
        if not provider:
            raise HTTPException(status_code=400, detail="Provider is required")
        network = build_service_pricing_key(tx_type, provider)
    else:
        if not network:
            raise HTTPException(status_code=400, detail="Network is required")

    rule = db.query(PricingRule).filter(PricingRule.network == network, PricingRule.role == role).first()
    margin_type_raw = str(getattr(payload, "margin_type", None) or "fixed").strip().lower()
    if margin_type_raw not in ("fixed", "percentage"):
        margin_type_raw = "fixed"
    if not rule:
        rule = PricingRule(network=network, role=role, margin=payload.margin, margin_type=margin_type_raw)
        db.add(rule)
    else:
        rule.margin = payload.margin
        rule.margin_type = margin_type_raw
    audit_log = AdminAuditLog(
        admin_email=admin.email,
        action="pricing_rule_update",
        target=network,
        details={"role": role.value, "margin": float(payload.margin), "margin_type": margin_type_raw},
    )
    db.add(audit_log)
    db.commit()
    return {"status": "ok", "network": network}


@router.get("/pricing", response_model=PricingRulesResponse)
def list_pricing(admin=Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(PricingRule).order_by(PricingRule.network.asc(), PricingRule.role.asc()).all()
    items = []
    for row in rows:
        parsed = parse_pricing_key(row.network)
        items.append(
            {
                "id": row.id,
                "network": row.network,
                "tx_type": parsed["tx_type"],
                "provider": parsed.get("provider"),
                "role": row.role.value if hasattr(row.role, "value") else str(row.role),
                "margin": row.margin,
                "margin_type": str(getattr(row, "margin_type", None) or "fixed"),
                "kind": parsed["kind"],
            }
        )
    return {"items": items}


@router.post("/users/{user_id}/suspend")

def suspend_user(user_id: int, admin=Depends(require_admin), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_active = False
    db.commit()
    return {"status": "suspended"}


@router.post("/users/{user_id}/activate")
def activate_user(user_id: int, admin=Depends(require_admin), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_active = True
    db.commit()
    return {"status": "active"}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Suffix email and phone to allow future registration with same credentials
    suffix = f"_deleted_{int(datetime.now(timezone.utc).timestamp())}"
    
    if user.email:
        user.email = f"{user.email}{suffix}"
    if user.phone_number:
        user.phone_number = f"{user.phone_number}{suffix}"

    user.is_active = False
    user.reset_token = None
    user.verification_token = None
    
    audit_log = AdminAuditLog(
        admin_email=admin.email,
        action="user_delete",
        target=str(user_id),
        details={"email_before": user.email.replace(suffix, ""), "id": user_id},
    )
    db.add(audit_log)
    db.commit()
    return {"status": "ok", "message": "User deleted successfully"}



@router.post("/wallets/adjust")
def adjust_user_wallet(
    payload: AdjustWalletRequest, admin=Depends(require_admin), db: Session = Depends(get_db)
):
    if payload.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    
    wallet = get_or_create_wallet(db, payload.user_id)
    if not wallet:
        raise HTTPException(status_code=404, detail="User wallet not found")
    
    if payload.action == "credit":
        credit_wallet(db, wallet, payload.amount, f"ADMIN_ADJUST_{payload.user_id}_CREDIT", f"Admin credit: {payload.reason}")
    elif payload.action == "debit":
        if wallet.balance < payload.amount:
            raise HTTPException(status_code=400, detail="Insufficient balance for debit")
        debit_wallet(db, wallet, payload.amount, f"ADMIN_ADJUST_{payload.user_id}_DEBIT", f"Admin debit: {payload.reason}")
    else:
        raise HTTPException(status_code=400, detail="Invalid action")
    
    audit_log = AdminAuditLog(
        admin_email=admin.email,
        action=f"wallet_adjust_{payload.action}",
        target=str(payload.user_id),
        details={"amount": float(payload.amount), "reason": payload.reason}
    )
    db.add(audit_log)
    db.commit()
    return {"status": "ok", "new_balance": wallet.balance}


@router.get("/services/toggles", response_model=list[ServiceToggleOut])
def get_service_toggles(admin=Depends(require_admin), db: Session = Depends(get_db)):
    toggles = db.query(ServiceToggle).all()
    return toggles


@router.patch("/services/toggles/{service_name}", response_model=ServiceToggleOut)
def update_service_toggle(
    service_name: str, payload: ServiceToggleUpdate, admin=Depends(require_admin), db: Session = Depends(get_db)
):
    toggle = db.query(ServiceToggle).filter(ServiceToggle.service_name == service_name).first()
    if not toggle:
        toggle = ServiceToggle(service_name=service_name, is_active=payload.is_active)
        db.add(toggle)
    else:
        toggle.is_active = payload.is_active
        
    audit_log = AdminAuditLog(
        admin_email=admin.email,
        action="service_toggle_update",
        target=service_name,
        details={"is_active": payload.is_active}
    )
    db.add(audit_log)
    db.commit()
    db.refresh(toggle)
    return toggle


@router.get("/data-plans")
def get_all_data_plans(admin=Depends(require_admin), db: Session = Depends(get_db)):
    plans = db.query(DataPlan).order_by(DataPlan.network.asc(), DataPlan.plan_name.asc()).all()
    result = []
    for p in plans:
        result.append({
            "id": p.id,
            "network": p.network,
            "plan_code": p.plan_code,
            "plan_name": p.plan_name,
            "data_size": p.data_size,
            "validity": p.validity,
            "base_price": float(p.base_price),
            "display_price": float(p.display_price) if p.display_price is not None else None,
            "is_active": p.is_active,
            "provider": p.provider,
            "provider_plan_id": p.provider_plan_id,
            "created_at": p.created_at,
            "updated_at": p.updated_at,
        })
    return result


@router.patch("/data-plans/{plan_id}")
def update_data_plan(
    plan_id: int, payload: DataPlanUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    plan = db.query(DataPlan).filter(DataPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Data plan not found")

    changes: dict = {}

    if payload.plan_name is not None:
        plan.plan_name = payload.plan_name
        changes["plan_name"] = payload.plan_name

    if payload.data_size is not None:
        plan.data_size = payload.data_size
        changes["data_size"] = payload.data_size

    if payload.validity is not None:
        plan.validity = payload.validity
        changes["validity"] = payload.validity

    if payload.base_price is not None:
        plan.base_price = payload.base_price
        changes["base_price"] = float(payload.base_price)

    if payload.is_active is not None and payload.is_active != plan.is_active:
        plan.is_active = payload.is_active
        changes["is_active"] = payload.is_active

    if payload.clear_display_price:
        if plan.display_price is not None:
            plan.display_price = None
            changes["display_price"] = None
    elif payload.display_price is not None:
        if payload.display_price < 0:
            raise HTTPException(status_code=400, detail="display_price must be >= 0")
        plan.display_price = payload.display_price
        changes["display_price"] = float(payload.display_price)

    if not changes:
        return {
            "status": "no_change",
            "id": plan.id,
            "is_active": plan.is_active,
            "display_price": float(plan.display_price) if plan.display_price is not None else None,
            "plan_name": plan.plan_name
        }

    audit_log = AdminAuditLog(
        admin_email=admin.email,
        action="data_plan_update",
        target=str(plan_id),
        details={"plan_code": plan.plan_code, "network": plan.network, **changes},
    )
    db.add(audit_log)
    db.commit()
    # _invalidate_plans_cache()
    return {
        "status": "ok",
        "id": plan.id,
        "is_active": plan.is_active,
        "display_price": float(plan.display_price) if plan.display_price is not None else None,
    }


@router.get("/referrals", response_model=AdminReferralsResponse)
def get_all_referrals(
    page: int = 1, page_size: int = 50, admin=Depends(require_admin), db: Session = Depends(get_db)
):
    query = db.query(Referral)
    total = query.count()
    items = query.order_by(Referral.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size
    }


@router.get("/audit-logs", response_model=AdminAuditLogsResponse)
def get_audit_logs(
    page: int = 1, page_size: int = 50, admin=Depends(require_admin), db: Session = Depends(get_db)
):
    query = db.query(AdminAuditLog)
    total = query.count()
    items = []
    for log in query.order_by(AdminAuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all():
        items.append({
            "id": log.id,
            "admin_email": log.admin_email,
            "action": log.action,
            "target": log.target,
            "details": log.details,
            "created_at": log.created_at,
        })
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size
    }

@router.post("/data-plans/clean-legacy")
def clean_legacy_data_plans(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """
    Deletes legacy data plans that do not follow the new provider:network:id canonical format,
    which usually show up as 'unknown' provider.
    """
    plans = db.query(DataPlan).all()
    deleted = 0
    for plan in plans:
        code = str(plan.plan_code or "")
        colons = code.count(":")
        provider = str(plan.provider or "").strip()
        
        if colons < 2 or not provider or provider.lower() == "unknown":
            db.delete(plan)
            deleted += 1
            
    db.commit()
    try:
        _invalidate_plans_cache()
    except Exception:
        pass
    return {"status": "ok", "message": f"Successfully deleted {deleted} legacy/unknown data plans!"}

@router.post("/data-plans")
def create_data_plan(payload: DataPlanUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """
    Manually create a new data plan.
    """
    # Reuse DataPlanUpdate but we need to ensure required fields for creation are present
    if not payload.network or not payload.plan_code or not payload.plan_name:
        raise HTTPException(status_code=400, detail="Network, plan_code, and plan_name are required.")
    
    # Check if plan_code already exists
    existing = db.query(DataPlan).filter(DataPlan.plan_code == payload.plan_code).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Plan code {payload.plan_code} already exists.")

    plan = DataPlan(
        network=payload.network,
        plan_code=payload.plan_code,
        plan_name=payload.plan_name,
        data_size=payload.data_size or "—",
        validity=payload.validity or "30 Days",
        base_price=payload.base_price or Decimal("0"),
        display_price=payload.display_price,
        is_active=payload.is_active if payload.is_active is not None else True,
        provider=payload.provider,
        provider_plan_id=payload.provider_plan_id,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    
    try:
        _invalidate_plans_cache()
    except Exception:
        pass
        
    return plan
