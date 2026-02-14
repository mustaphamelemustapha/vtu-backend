from pydantic import BaseModel
from decimal import Decimal
from datetime import datetime
from typing import Optional

from app.models.transaction import TransactionStatus, TransactionType
from app.models.user import UserRole


class FundUserWalletRequest(BaseModel):
    user_id: int
    amount: Decimal
    description: str = "Admin funding"


class PricingRuleUpdate(BaseModel):
    network: str
    role: str
    margin: Decimal


class AdminTransactionOut(BaseModel):
    id: int
    created_at: datetime
    user_id: int
    user_email: str
    reference: str
    tx_type: TransactionType
    amount: Decimal
    status: TransactionStatus
    network: Optional[str] = None
    data_plan_code: Optional[str] = None
    external_reference: Optional[str] = None
    failure_reason: Optional[str] = None


class AdminTransactionsResponse(BaseModel):
    items: list[AdminTransactionOut]
    total: int
    page: int
    page_size: int


class AdminUserOut(BaseModel):
    id: int
    created_at: datetime
    email: str
    full_name: str
    role: UserRole
    is_active: bool
    is_verified: bool


class AdminUsersResponse(BaseModel):
    items: list[AdminUserOut]
    total: int
    page: int
    page_size: int
