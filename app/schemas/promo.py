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

class PromoCodeOut(PromoCodeBase):
    id: int
    created_at: datetime
    
    class Config:
        orm_mode = True

class UserPromoOut(BaseModel):
    id: int
    status: str
    used_at: Optional[datetime] = None
    promo_code: PromoCodeOut
    
    class Config:
        orm_mode = True

class ClaimPromoRequest(BaseModel):
    code: str = Field(..., description="The promo code to claim")
