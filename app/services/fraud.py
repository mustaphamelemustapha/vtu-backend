from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, inspect
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import ServiceTransaction, Transaction, TransactionStatus, TransactionType

settings = get_settings()

PURCHASE_SUCCESS_LIKE = {TransactionStatus.PENDING, TransactionStatus.SUCCESS}
SERVICE_SUCCESS_LIKE = {
    TransactionStatus.PENDING.value,
    TransactionStatus.SUCCESS.value,
}


def _begin_day_utc() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _as_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _fraud_error(message: str, code: str, hint: str, status_code: int = 429) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "message": message,
            "code": code,
            "hint": hint,
        },
    )


def enforce_purchase_limits(
    db: Session,
    *,
    user_id: int,
    amount: Decimal,
    tx_type: str,
) -> None:
    if not settings.fraud_guard_enabled:
        return

    amount = _as_decimal(amount)
    if amount <= 0:
        raise _fraud_error(
            "Invalid purchase amount.",
            "FRAUD_INVALID_AMOUNT",
            "Use a valid amount greater than zero.",
            status_code=400,
        )

    if amount > settings.fraud_single_tx_limit_ngn:
        limit = f"₦{settings.fraud_single_tx_limit_ngn}"
        raise _fraud_error(
            f"This transaction exceeds your single-purchase limit ({limit}).",
            "FRAUD_SINGLE_TX_LIMIT",
            "Split the purchase into smaller amounts or contact support.",
        )

    start_of_day = _begin_day_utc()
    base_total = (
        db.query(func.coalesce(func.sum(Transaction.amount), 0))
        .filter(
            Transaction.user_id == user_id,
            Transaction.created_at >= start_of_day,
            Transaction.status.in_(PURCHASE_SUCCESS_LIKE),
            Transaction.tx_type != TransactionType.WALLET_FUND,
        )
        .scalar()
        or 0
    )
    base_count = (
        db.query(func.count(Transaction.id))
        .filter(
            Transaction.user_id == user_id,
            Transaction.created_at >= start_of_day,
            Transaction.status.in_(PURCHASE_SUCCESS_LIKE),
            Transaction.tx_type != TransactionType.WALLET_FUND,
        )
        .scalar()
        or 0
    )

    service_total = 0
    service_count = 0
    try:
        if inspect(db.bind).has_table("service_transactions"):
            service_total = (
                db.query(func.coalesce(func.sum(ServiceTransaction.amount), 0))
                .filter(
                    ServiceTransaction.user_id == user_id,
                    ServiceTransaction.created_at >= start_of_day,
                    ServiceTransaction.status.in_(SERVICE_SUCCESS_LIKE),
                )
                .scalar()
                or 0
            )
            service_count = (
                db.query(func.count(ServiceTransaction.id))
                .filter(
                    ServiceTransaction.user_id == user_id,
                    ServiceTransaction.created_at >= start_of_day,
                    ServiceTransaction.status.in_(SERVICE_SUCCESS_LIKE),
                )
                .scalar()
                or 0
            )
    except Exception:
        # Do not block purchases when service table metadata is unavailable.
        service_total = 0
        service_count = 0

    daily_total = _as_decimal(base_total) + _as_decimal(service_total)
    daily_count = int(base_count) + int(service_count)

    if daily_count + 1 > settings.fraud_daily_purchase_count_limit:
        raise _fraud_error(
            "Daily purchase count limit reached.",
            "FRAUD_DAILY_COUNT_LIMIT",
            "Try again tomorrow or contact support if this is expected activity.",
        )

    if daily_total + amount > settings.fraud_daily_total_limit_ngn:
        limit = f"₦{settings.fraud_daily_total_limit_ngn}"
        raise _fraud_error(
            f"Daily purchase value limit reached ({limit}).",
            "FRAUD_DAILY_TOTAL_LIMIT",
            "Lower the amount or try again tomorrow.",
        )
