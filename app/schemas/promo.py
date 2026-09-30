from pydantic import BaseModel, Field
from typing import Optional, List
from decimal import Decimal
from datetime import datetime

class PromoCodeBase(BaseModel):
    code: str
    description: Optional[str] = None
    discount_amount: Decimal
    is_percentage: bool
    max_uses_per_user: int
    expires_at: Optional[datetime] = None
    is_active: bool
    applicable_network: str = "ALL"
    applicable_plan_size: str = "ALL"
    target_audience: str = "ALL_USERS"

class PromoCodeOut(PromoCodeBase):
    id: int
    created_at: datetime
    
    class Config:
        orm_mode = True

class PromoCodeCreate(PromoCodeBase):
    max_total_uses: Optional[int] = None

class UserPromoOut(BaseModel):
    id: int
    status: str
    used_at: Optional[datetime] = None
    promo_code: PromoCodeOut
    
    class Config:
        orm_mode = True

class ClaimPromoRequest(BaseModel):
    code: str = Field(..., description="The promo code to claim")
