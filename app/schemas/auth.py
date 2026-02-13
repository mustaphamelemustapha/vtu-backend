from pydantic import BaseModel, EmailStr, validator
from typing import Optional

def _validate_password_length(value: str) -> str:
    if len(value.encode("utf-8")) > 72:
        raise ValueError("Password too long (max 72 bytes)")
    return value


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RegisterRequest(BaseModel):
    email: EmailStr
    full_name: str
    password: str

    _password_len = validator("password", allow_reuse=True)(_validate_password_length)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str

    _password_len = validator("password", allow_reuse=True)(_validate_password_length)


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


class Message(BaseModel):
    message: str


class EmailVerification(BaseModel):
    token: str
