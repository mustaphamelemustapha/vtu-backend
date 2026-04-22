from pydantic import BaseModel, Field
from decimal import Decimal
from typing import Optional


class DataPlanOut(BaseModel):
    id: int
    network: str
    plan_code: str
    plan_name: str
    data_size: str
    validity: str
    price: Decimal

    class Config:
        orm_mode = True


class BuyDataRequest(BaseModel):
    client_request_id: Optional[str] = Field(default=None, max_length=128)
    plan_code: str
    phone_number: str
    ported_number: bool = False
    network: Optional[str] = None
