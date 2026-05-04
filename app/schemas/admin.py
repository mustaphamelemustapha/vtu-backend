from pydantic import BaseModel
from decimal import Decimal
from datetime import datetime
from typing import Optional, List

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
    margin_type: Optional[str] = "fixed"  # 'fixed' or 'percentage'


class PricingRuleOut(BaseModel):
    id: int
    network: str
    tx_type: str
    provider: Optional[str] = None
    role: str
    margin: Decimal
    margin_type: str = "fixed"
    kind: str
    class Config:
        orm_mode = True


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
    class Config:
        orm_mode = True


class AdminTransactionsResponse(BaseModel):
    items: list[AdminTransactionOut]
    total: int
    page: int
    page_size: int


class AdminUserOut(BaseModel):
    id: int
    created_at: datetime
    email: str
    phone_number: Optional[str] = None
    full_name: str
    role: UserRole
    is_active: bool
    is_verified: bool
    class Config:
        orm_mode = True


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
    class Config:
        orm_mode = True


class AdminReportsResponse(BaseModel):
    items: list[AdminReportOut]
    total: int
    page: int
    page_size: int


class AdminReportActionRequest(BaseModel):
    status: Optional[str] = None
    admin_note: Optional[str] = None


class AdjustWalletRequest(BaseModel):
    user_id: int
    amount: Decimal
    action: str  # "credit" or "debit"
    reason: str


class ReconcileTransactionRequest(BaseModel):
    reference: str
    note: Optional[str] = None


class ReconcileTransactionsBulkRequest(BaseModel):
    references: List[str]
    note: Optional[str] = None


class ServiceToggleUpdate(BaseModel):
    is_active: bool


class ServiceToggleOut(BaseModel):
    id: int
    service_name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    class Config:
        orm_mode = True


class DataPlanUpdate(BaseModel):
    is_active: Optional[bool] = None
    # Admin can set an explicit display price; set to null to clear (fall back to margin).
    display_price: Optional[Decimal] = None
    clear_display_price: bool = False


class AdminDataPlanOut(BaseModel):
    id: int
    network: str
    plan_code: str
    plan_name: str
    data_size: str
    validity: str
    base_price: Decimal
    display_price: Optional[Decimal] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    class Config:
        orm_mode = True


class AdminAuditLogOut(BaseModel):
    id: int
    admin_email: str
    action: str
    target: Optional[str] = None
    details: Optional[dict] = None
    created_at: datetime
    class Config:
        orm_mode = True


class AdminAuditLogsResponse(BaseModel):
    items: list[AdminAuditLogOut]
    total: int
    page: int
    page_size: int


class AdminReferralOut(BaseModel):
    id: int
    referrer_id: int
    referrer_email: str
    referred_id: int
    referred_email: str
    status: str
    reward_amount: Decimal
    first_deposit_amount: Optional[Decimal] = None
    created_at: datetime
    class Config:
        orm_mode = True


class AdminReferralsResponse(BaseModel):
    items: list[AdminReferralOut]
    total: int
    page: int
    page_size: int
