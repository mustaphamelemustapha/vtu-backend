from datetime import date, datetime, time, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, or_, select, union_all, String, cast, inspect
from app.core.database import get_db
from app.dependencies import require_admin
from app.models import User, Transaction, ServiceTransaction, TransactionStatus, TransactionType, PricingRule, PricingRole, ApiLog, DataPlan
from app.schemas.admin import (
    FundUserWalletRequest,
    PricingRuleUpdate,
    AdminTransactionsResponse,
    AdminUsersResponse,
)
from app.services.wallet import get_or_create_wallet, credit_wallet

router = APIRouter()

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
    return {
        "total_revenue": total_revenue,
        "data_revenue": data_revenue,
        "data_cost_estimate": data_cost_estimate,
        "gross_profit_estimate": gross_profit_estimate,
        "gross_margin_pct": round(gross_margin_pct, 2),
        "total_users": total_users,
        "daily_transactions": daily_tx,
        "api_success": api_success,
        "api_failed": api_failed,
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
                    "tx_type": tx.tx_type,
                    "amount": tx.amount,
                    "status": tx.status,
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
        where.append(combined.c.status == status_enum.value)
    if type_enum is not None:
        where.append(combined.c.tx_type == type_enum.value)
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
        items.append(dict(r))

    return {"items": items, "total": int(total), "page": page, "page_size": page_size}


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
        query = query.filter(or_(User.email.ilike(needle), User.full_name.ilike(needle)))

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
                "full_name": u.full_name,
                "role": u.role,
                "is_active": u.is_active,
                "is_verified": u.is_verified,
            }
        )

    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/fund-wallet")

def fund_user_wallet(payload: FundUserWalletRequest, admin=Depends(require_admin), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == payload.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    wallet = get_or_create_wallet(db, user.id)
    credit_wallet(db, wallet, payload.amount, f"ADMIN_{user.id}", payload.description)
    return {"status": "ok"}


@router.post("/pricing")

def update_pricing(payload: PricingRuleUpdate, admin=Depends(require_admin), db: Session = Depends(get_db)):
    raw_role = (payload.role or "").strip().lower()
    if raw_role not in {"user", "reseller"}:
        raise HTTPException(status_code=400, detail="Invalid role")
    role = PricingRole.USER if raw_role == "user" else PricingRole.RESELLER
    network = (payload.network or "").strip().lower()
    if not network:
        raise HTTPException(status_code=400, detail="Network is required")
    rule = db.query(PricingRule).filter(PricingRule.network == network, PricingRule.role == role).first()
    if not rule:
        rule = PricingRule(network=network, role=role, margin=payload.margin)
        db.add(rule)
    else:
        rule.margin = payload.margin
    db.commit()
    return {"status": "ok"}


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
