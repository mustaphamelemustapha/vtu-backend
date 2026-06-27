from pydantic import BaseModel, Field
from typing import Optional
from decimal import Decimal

# Pydantic v1 since project uses Pydantic v1.x

class DeveloperStatusResponse(BaseModel):
    is_developer: bool
    developer_status: str
    api_public_key: Optional[str] = None
    has_keys: bool

    class Config:
        orm_mode = True


class DeveloperApplyRequest(BaseModel):
    additional_info: Optional[str] = Field(None, description="Any extra info about the developer application")


class ApiKeyResponse(BaseModel):
    api_public_key: str
    api_secret_key: str


class DeveloperWalletBalanceResponse(BaseModel):
    balance: Decimal
    currency: str = "NGN"


class DeveloperDataPurchaseRequest(BaseModel):
    phone_number: str = Field(..., description="Recipient phone number")
    network: str = Field(..., description="MTN, GLO, AIRTEL, or 9MOBILE")
    plan_id: int = Field(..., description="The numerical plan ID")
    reference: str = Field(..., description="Unique developer transaction reference")


class DeveloperAirtimePurchaseRequest(BaseModel):
    phone_number: str = Field(..., description="Recipient phone number")
    network: str = Field(..., description="MTN, GLO, AIRTEL, or 9MOBILE")
    amount: Decimal = Field(..., description="Amount of airtime to buy")
    reference: str = Field(..., description="Unique developer transaction reference")


class DeveloperPurchaseResponse(BaseModel):
    status: str
    reference: str
    amount: Decimal
    message: str
