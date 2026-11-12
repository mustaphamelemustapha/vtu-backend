from pydantic import BaseModel, Field
from typing import Optional
from decimal import Decimal

# Pydantic v1 since project uses Pydantic v1.x

class DeveloperStatusResponse(BaseModel):
    is_developer: bool
    developer_status: str
    api_public_key: Optional[str] = None
    test_api_public_key: Optional[str] = None
    has_keys: bool
    api_secret_key: Optional[str] = None
    test_api_secret_key: Optional[str] = None
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
    test_api_public_key: Optional[str] = None
    test_api_secret_key: Optional[str] = None


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
from pydantic import root_validator

class DeveloperDataPurchaseRequest(BaseModel):
    phone_number: str = Field(..., description="Recipient phone number")
    network: str = Field(..., description="MTN, GLO, AIRTEL, or 9MOBILE")
    plan_id: Optional[Union[int, str]] = Field(None, description="The numerical plan ID")
    plan_code: Optional[str] = Field(None, description="Plan code as alternative to plan_id")
    reference: Optional[str] = Field(None, description="Unique developer transaction reference")
    ported_number: Optional[bool] = Field(False, description="Whether the phone number is ported")

    @root_validator(pre=True)
    def normalize_data_payload(cls, values):
        if not isinstance(values, dict):
            return values

        # 1. Phone number aliases (phone, mobile_number, recipient, number)
        phone = (
            values.get("phone_number")
            or values.get("phone")
            or values.get("mobile_number")
            or values.get("recipient")
            or values.get("number")
        )
        if phone:
            values["phone_number"] = str(phone).strip()

        # 2. Plan identifier aliases (plan, plan_id, plan_code, data_plan)
        plan = (
            values.get("plan_id")
            or values.get("plan")
            or values.get("plan_code")
            or values.get("data_plan")
        )
        if plan is not None:
            values["plan_id"] = plan
            values["plan_code"] = str(plan)

        # 3. Network identifier aliases (network, network_id, network_name)
        # Standard Nigerian mappings: 1=MTN, 2=GLO, 3=9MOBILE, 4=AIRTEL
        net = values.get("network") or values.get("network_id") or values.get("network_name")
        if net is not None:
            net_str = str(net).strip().lower()
            if net_str in ("1", "mtn"):
                values["network"] = "MTN"
            elif net_str in ("2", "glo"):
                values["network"] = "GLO"
            elif net_str in ("3", "9mobile", "etisalat"):
                values["network"] = "9MOBILE"
            elif net_str in ("4", "airtel"):
                values["network"] = "AIRTEL"
            else:
                values["network"] = str(net).upper()

        # 4. Reference aliases (reference, request-id, request_id, ident, ref)
        ref = (
            values.get("reference")
            or values.get("request-id")
            or values.get("request_id")
            or values.get("ident")
            or values.get("ref")
        )
        if not ref:
            import secrets
            ref = f"REQ_{secrets.token_hex(8).upper()}"
        values["reference"] = str(ref).strip()

        # 5. Ported number / bypass flag
        ported = (
            values.get("ported_number")
            or values.get("Ported_number")
            or values.get("bypass")
            or values.get("ported")
        )
        if ported is not None:
            values["ported_number"] = bool(ported)

        if not values.get("phone_number"):
            raise ValueError("Recipient phone number is required (pass 'phone_number', 'phone', or 'mobile_number').")
        if not values.get("plan_id") and not values.get("plan_code"):
            raise ValueError("Plan ID is required (pass 'plan_id' or 'plan').")
        if not values.get("network"):
            raise ValueError("Network is required (pass 'network' e.g. MTN, GLO, AIRTEL, 9MOBILE).")

        return values


class DeveloperAirtimePurchaseRequest(BaseModel):
    phone_number: str = Field(..., description="Recipient phone number")
    network: str = Field(..., description="MTN, GLO, AIRTEL, or 9MOBILE")
    amount: Decimal = Field(..., description="Amount of airtime to buy")
    reference: Optional[str] = Field(None, description="Unique developer transaction reference")

    @root_validator(pre=True)
    def normalize_airtime_payload(cls, values):
        if not isinstance(values, dict):
            return values

        # 1. Phone number aliases
        phone = (
            values.get("phone_number")
            or values.get("phone")
            or values.get("mobile_number")
            or values.get("recipient")
            or values.get("number")
        )
        if phone:
            values["phone_number"] = str(phone).strip()

        # 2. Network identifier aliases
        net = values.get("network") or values.get("network_id") or values.get("network_name")
        if net is not None:
            net_str = str(net).strip().lower()
            if net_str in ("1", "mtn"):
                values["network"] = "MTN"
            elif net_str in ("2", "glo"):
                values["network"] = "GLO"
            elif net_str in ("3", "9mobile", "etisalat"):
                values["network"] = "9MOBILE"
            elif net_str in ("4", "airtel"):
                values["network"] = "AIRTEL"
            else:
                values["network"] = str(net).upper()

        # 3. Amount aliases
        amt = values.get("amount") or values.get("airtime_amount")
        if amt is not None:
            values["amount"] = Decimal(str(amt))

        # 4. Reference aliases
        ref = (
            values.get("reference")
            or values.get("request-id")
            or values.get("request_id")
            or values.get("ident")
            or values.get("ref")
        )
        if not ref:
            import secrets
            ref = f"AIR_{secrets.token_hex(8).upper()}"
        values["reference"] = str(ref).strip()

        if not values.get("phone_number"):
            raise ValueError("Recipient phone number is required (pass 'phone_number', 'phone', or 'mobile_number').")
        if not values.get("network"):
            raise ValueError("Network is required (pass 'network' e.g. MTN, GLO, AIRTEL, 9MOBILE).")
        if not values.get("amount"):
            raise ValueError("Amount is required.")

        return values


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
