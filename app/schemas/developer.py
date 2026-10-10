from pydantic import BaseModel, Field
from typing import Optional
from decimal import Decimal

# Pydantic v1 since project uses Pydantic v1.x

class DeveloperStatusResponse(BaseModel):
    is_developer: bool
    developer_status: str
    api_public_key: Optional[str] = None
    has_keys: bool
    api_secret_key: Optional[str] = None
    webhook_url: Optional[str] = None
    webhook_secret_prefix: Optional[str] = None

    class Config:
        orm_mode = True

class WebhookConfigRequest(BaseModel):
    webhook_url: str = Field(..., description="The URL where we will send webhooks")


class DeveloperApplyRequest(BaseModel):
    additional_info: Optional[str] = Field(None, description="Any extra info about the developer application")


class ApiKeyResponse(BaseModel):
    api_public_key: str
    api_secret_key: str


from typing import List, Any

class DeveloperWalletBalanceData(BaseModel):
    balance: Decimal
    currency: str = "NGN"

class DeveloperWalletBalanceResponse(BaseModel):
    status: bool
    message: str
    data: DeveloperWalletBalanceData

class DeveloperDataPlanItem(BaseModel):
    plan_id: int
    plan_code: str
    network: str
    plan_name: str
    data_size: str
    validity: str
    price: float

class DeveloperDataPlansData(BaseModel):
    plans: List[DeveloperDataPlanItem]

class DeveloperDataPlansResponse(BaseModel):
    status: bool
    message: str
    data: DeveloperDataPlansData

class DeveloperDataStatusData(BaseModel):
    reference: str
    status: str
    network: str
    mobile_number: str
    plan: Optional[str] = None
    amount_charged: float
    message: str
    purchased_at: Optional[str] = None
    queried_at: str
    livemode: bool
    mode: str

class DeveloperDataStatusResponse(BaseModel):
    status: bool
    message: str
    data: DeveloperDataStatusData


from typing import Optional, Union

class DeveloperDataPurchaseRequest(BaseModel):
    phone_number: str = Field(..., description="Recipient phone number")
    network: str = Field(..., description="MTN, GLO, AIRTEL, or 9MOBILE")
    plan_id: Union[int, str] = Field(..., description="The numerical plan ID")
    reference: str = Field(..., description="Unique developer transaction reference")


class DeveloperAirtimePurchaseRequest(BaseModel):
    phone_number: str = Field(..., description="Recipient phone number")
    network: str = Field(..., description="MTN, GLO, AIRTEL, or 9MOBILE")
    amount: Decimal = Field(..., description="Amount of airtime to buy")
    reference: str = Field(..., description="Unique developer transaction reference")


class DeveloperPurchaseData(BaseModel):
    status: str
    reference: str
    amount: float
    network: str
    phone_number: str

class DeveloperPurchaseResponse(BaseModel):
    status: bool
    message: str
    data: DeveloperPurchaseData
