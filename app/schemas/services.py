from pydantic import BaseModel, Field
from decimal import Decimal
from typing import Any, Optional


class AirtimePurchaseRequest(BaseModel):
    client_request_id: Optional[str] = Field(default=None, max_length=128)
    network: str = Field(..., min_length=2, max_length=32)
    phone_number: str = Field(..., min_length=7, max_length=20)
    amount: Decimal = Field(..., gt=0)


class CablePurchaseRequest(BaseModel):
    client_request_id: Optional[str] = Field(default=None, max_length=128)
    provider: str = Field(..., min_length=2, max_length=64)
    smartcard_number: str = Field(..., min_length=5, max_length=32)
    phone_number: str = Field(..., min_length=7, max_length=20)
    package_code: str = Field(..., min_length=1, max_length=64)
    amount: Decimal = Field(..., gt=0)


class CableVerifyRequest(BaseModel):
    provider: str = Field(..., min_length=2, max_length=64)
    smartcard_number: str = Field(..., min_length=5, max_length=32)


class ElectricityPurchaseRequest(BaseModel):
    client_request_id: Optional[str] = Field(default=None, max_length=128)
    disco: str = Field(..., min_length=2, max_length=64)
    meter_number: str = Field(..., min_length=5, max_length=32)
    meter_type: str = Field(..., min_length=3, max_length=16)  # prepaid|postpaid
    phone_number: str = Field(..., min_length=7, max_length=20)
    amount: Decimal = Field(..., gt=0)


class ElectricityVerifyRequest(BaseModel):
    disco: str = Field(..., min_length=2, max_length=64)
    meter_number: str = Field(..., min_length=5, max_length=32)
    meter_type: str = Field(..., min_length=3, max_length=16)  # prepaid|postpaid


class ExamPurchaseRequest(BaseModel):
    client_request_id: Optional[str] = Field(default=None, max_length=128)
    exam: str = Field(..., min_length=2, max_length=64)
    exam_type: Optional[str] = Field(default=None, min_length=2, max_length=64)
    quantity: int = Field(1, ge=1, le=10)
    phone_number: Optional[str] = Field(default=None, min_length=7, max_length=20)


class ServicesCatalogOut(BaseModel):
    airtime_networks: list[str]
    cable_providers: list[dict[str, Any]]
    electricity_discos: list[str]
    exam_types: list[str]
