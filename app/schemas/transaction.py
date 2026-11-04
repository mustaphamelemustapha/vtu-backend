import json
from pydantic import BaseModel, Field, validator
from decimal import Decimal
from datetime import datetime
from typing import Any, Optional


class TransactionOut(BaseModel):
    id: int
    created_at: Optional[datetime] = None
    reference: str = ""
    network: Optional[str] = None
    data_plan_code: Optional[str] = None
    amount: Decimal = Decimal("0")
    status: str = "pending"
    tx_type: str = "data"
    external_reference: Optional[str] = None
    failure_reason: Optional[str] = None
    meta: Optional[dict[str, Any]] = None
    has_open_report: bool = False

    @validator("meta", pre=True)
    def parse_meta(cls, v):
        if isinstance(v, dict):
            return v
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
        return None

    @validator("status", "tx_type", pre=True)
    def parse_enum_str(cls, v):
        if hasattr(v, "value"):
            return str(v.value).lower()
        return str(v or "").lower()

    class Config:
        orm_mode = True


class TransactionReportRequest(BaseModel):
    category: str = Field(default="delivery_issue", min_length=3, max_length=32)
    reason: str = Field(..., min_length=6, max_length=1000)


class TransactionReportOut(BaseModel):
    id: int
    created_at: Optional[datetime] = None
    transaction_reference: str = ""
    tx_type: str = "data"
    category: str = "delivery_issue"
    reason: str = ""
    status: str = "open"
    admin_note: Optional[str] = None
    resolved_at: Optional[datetime] = None

    @validator("status", "tx_type", pre=True)
    def parse_enum_str(cls, v):
        if hasattr(v, "value"):
            return str(v.value).lower()
        return str(v or "").lower()

    class Config:
        orm_mode = True
