from pydantic import BaseModel
from decimal import Decimal
from app.models.transaction import TransactionStatus, TransactionType


class TransactionOut(BaseModel):
    id: int
    reference: str
    network: str | None
    data_plan_code: str | None
    amount: Decimal
    status: TransactionStatus
    tx_type: TransactionType
    external_reference: str | None
    failure_reason: str | None

    class Config:
        orm_mode = True
