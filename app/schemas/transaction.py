from pydantic import BaseModel, Field
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
    has_open_report: bool = False

    class Config:
        orm_mode = True


class TransactionReportRequest(BaseModel):
    category: str = Field(default="delivery_issue", min_length=3, max_length=32)
    reason: str = Field(..., min_length=6, max_length=1000)


class TransactionReportOut(BaseModel):
    id: int
    created_at: Optional[datetime] = None
    transaction_reference: str
    tx_type: str
    category: str
    reason: str
    status: str
    admin_note: Optional[str] = None
    resolved_at: Optional[datetime] = None
