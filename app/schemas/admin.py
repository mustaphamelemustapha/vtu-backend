from pydantic import BaseModel
from decimal import Decimal


class FundUserWalletRequest(BaseModel):
    user_id: int
    amount: Decimal
    description: str = "Admin funding"


class PricingRuleUpdate(BaseModel):
    network: str
    role: str
    margin: Decimal
