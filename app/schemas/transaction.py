from pydantic import BaseModel
from decimal import Decimal
from datetime import datetime
from typing import Any, Optional
from app.models.transaction import TransactionStatus, TransactionType


class TransactionOut(BaseModel):
    id: int
    created_at: Optional[datetime] = None
    reference: str
    network: str | None
    data_plan_code: str | None
    amount: Decimal
    status: TransactionStatus | str
    tx_type: TransactionType | str
    external_reference: str | None
    failure_reason: str | None
    meta: Optional[dict[str, Any]] = None

    class Config:
        orm_mode = True
