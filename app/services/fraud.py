from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

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
    _ = db
    _ = user_id
    _ = tx_type

    amount = _as_decimal(amount)
    if amount <= 0:
        raise _fraud_error(
            "Invalid purchase amount.",
            "FRAUD_INVALID_AMOUNT",
            "Use a valid amount greater than zero.",
            status_code=400,
        )
    # Unlimited mode: no single-transaction, daily count, or daily total caps.
    return
