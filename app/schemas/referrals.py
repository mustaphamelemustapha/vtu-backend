from decimal import Decimal
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class ReferralItemOut(BaseModel):
    id: int
    referred_user_name: str
    referral_code_used: str
    status: str
    first_deposit_amount: Decimal
    reward_amount: Decimal
    qualified_at: Optional[datetime] = None
    rewarded_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        orm_mode = True


class ReferralDashboardOut(BaseModel):
    referral_code: str
    referral_link: Optional[str] = None
    total_referrals: int
    rewarded_referrals: int
    total_earned: Decimal
    reward_amount: Decimal
    referrals: list[ReferralItemOut]


class ReferralValidationResponse(BaseModel):
    valid: bool
    referral_code: Optional[str] = None
    referrer_name: Optional[str] = None
