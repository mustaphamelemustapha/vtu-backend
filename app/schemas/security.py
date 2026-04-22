from pydantic import BaseModel, validator


def _validate_pin(value: str) -> str:
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    if len(digits) != 4:
        raise ValueError("PIN must be exactly 4 digits")
    return digits


class PinSetupRequest(BaseModel):
    pin: str
    confirm_pin: str

    _pin_len = validator("pin", allow_reuse=True)(_validate_pin)
    _confirm_pin_len = validator("confirm_pin", allow_reuse=True)(_validate_pin)


class PinVerifyRequest(BaseModel):
    pin: str

    _pin_len = validator("pin", allow_reuse=True)(_validate_pin)


class PinChangeRequest(BaseModel):
    current_pin: str
    new_pin: str
    confirm_pin: str

    _current_pin_len = validator("current_pin", allow_reuse=True)(_validate_pin)
    _new_pin_len = validator("new_pin", allow_reuse=True)(_validate_pin)
    _confirm_pin_len = validator("confirm_pin", allow_reuse=True)(_validate_pin)


class PinResetConfirmRequest(BaseModel):
    token: str
    new_pin: str
    confirm_pin: str

    _new_pin_len = validator("new_pin", allow_reuse=True)(_validate_pin)
    _confirm_pin_len = validator("confirm_pin", allow_reuse=True)(_validate_pin)


class PinStatusResponse(BaseModel):
    is_set: bool
    pin_length: int = 4


class PinResetTokenResponse(BaseModel):
    is_valid: bool


class MessageResponse(BaseModel):
    message: str
