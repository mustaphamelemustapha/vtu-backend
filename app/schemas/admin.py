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
    network: Optional[str] = None
    tx_type: Optional[str] = None
    provider: Optional[str] = None
    role: str = "user"
    margin: Decimal


class PricingRuleOut(BaseModel):
    id: int
    network: str
    tx_type: str
    provider: Optional[str] = None
    role: str
    margin: Decimal
    kind: str


class PricingRulesResponse(BaseModel):
    items: list[PricingRuleOut]


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


class AdminReportOut(BaseModel):
    id: int
    created_at: datetime
    user_id: int
    user_email: str
    transaction_reference: str
    tx_type: str
    category: str
    reason: str
    status: str
    admin_note: Optional[str] = None
    resolved_at: Optional[datetime] = None


class AdminReportsResponse(BaseModel):
    items: list[AdminReportOut]
    total: int
    page: int
    page_size: int


class AdminReportActionRequest(BaseModel):
    status: Optional[str] = None
    admin_note: Optional[str] = None
