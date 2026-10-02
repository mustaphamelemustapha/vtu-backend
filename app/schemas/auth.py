from pydantic import BaseModel, EmailStr, validator
from typing import Optional

def _validate_password_length(value: str) -> str:
    if len(value.encode("utf-8")) > 72:
        raise ValueError("Password too long (max 72 bytes)")
    return value


NIGERIAN_STATES = (
    "Abia", "Adamawa", "Akwa Ibom", "Anambra", "Bauchi", "Bayelsa", "Benue", "Borno",
    "Cross River", "Delta", "Ebonyi", "Edo", "Ekiti", "Enugu", "FCT", "Gombe",
    "Imo", "Jigawa", "Kaduna", "Kano", "Katsina", "Kebbi", "Kogi", "Kwara",
    "Lagos", "Nasarawa", "Niger", "Ogun", "Ondo", "Osun", "Oyo", "Plateau",
    "Rivers", "Sokoto", "Taraba", "Yobe", "Zamfara",
)
STATE_CANONICAL_MAP = {s.lower(): s for s in NIGERIAN_STATES}
STATE_CANONICAL_MAP["federal capital territory"] = "FCT"
STATE_CANONICAL_MAP["abuja"] = "FCT"


def validate_nigerian_state(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    canonical = STATE_CANONICAL_MAP.get(raw.lower())
    if not canonical:
        raise ValueError(f"Invalid Nigerian state: '{value}'. Must be one of the 36 states or FCT.")
    return canonical


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RegisterRequest(BaseModel):
    email: EmailStr
    full_name: str
    phone_number: Optional[str] = None
    password: str
    referral_code: Optional[str] = None
    state: Optional[str] = None
    pin: Optional[str] = None

    _password_len = validator("password", allow_reuse=True)(_validate_password_length)

    @validator("state", pre=True, always=True)
    def _validate_state(cls, v):
        return validate_nigerian_state(v)

    @validator("pin", pre=True, always=True)
    def _validate_pin(cls, v):
        if v is None:
            return None
        pin_str = "".join(ch for ch in str(v) if ch.isdigit())
        if len(pin_str) != 4:
            raise ValueError("PIN must be exactly 4 digits")
        return pin_str


class LoginRequest(BaseModel):
    email: str
    password: str

    _password_len = validator("password", allow_reuse=True)(_validate_password_length)


class LookupRequest(BaseModel):
    identifier: str


class RefreshRequest(BaseModel):
    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    message: str
    reset_token: Optional[str] = None


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

    _password_len = validator("new_password", allow_reuse=True)(_validate_password_length)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    _password_len = validator("new_password", allow_reuse=True)(_validate_password_length)


class UpdateMeRequest(BaseModel):
    full_name: Optional[str] = None
    phone_number: Optional[str] = None
    state: Optional[str] = None

    @validator("state", pre=True, always=True)
    def _validate_state(cls, v):
        return validate_nigerian_state(v)


class Message(BaseModel):
    message: str


class EmailVerification(BaseModel):
    token: str


class FCMTokenRequest(BaseModel):
    fcm_token: str
