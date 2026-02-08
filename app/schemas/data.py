from pydantic import BaseModel
from decimal import Decimal


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
    plan_code: str
    phone_number: str
