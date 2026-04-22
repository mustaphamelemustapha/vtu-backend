from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from secrets import compare_digest

from app.core.security import hash_pin, verify_pin

PIN_LENGTH = 4


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_pin(value: str | None) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits if len(digits) == PIN_LENGTH else ""


def hash_pin_reset_token(token: str) -> str:
    return sha256((token or "").encode("utf-8")).hexdigest()


def clear_pin_state(user) -> None:
    user.pin_failed_attempts = 0
    user.pin_locked_until = None


def verify_pin_for_user(user, entered_pin: str) -> tuple[bool, str | None]:
    pin_hash_value = getattr(user, "pin_hash", None)
    if not pin_hash_value:
        return False, "Transaction PIN is not set"

    normalized = normalize_pin(entered_pin)
    if not normalized:
        return False, "Incorrect PIN, try again."

    if verify_pin(normalized, pin_hash_value):
        clear_pin_state(user)
        return True, None

    return False, "Incorrect PIN, try again."


def pin_status_payload(user) -> dict:
    return {
        "is_set": bool(getattr(user, "pin_hash", None)),
        "pin_length": PIN_LENGTH,
    }


def can_reset_pin(user) -> bool:
    return bool(getattr(user, "pin_hash", None))


def validate_reset_token(user, token: str) -> bool:
    expected = getattr(user, "pin_reset_token_hash", None)
    if not expected:
        return False
    provided = hash_pin_reset_token(token)
    return compare_digest(expected, provided)


def set_pin(user, pin: str) -> None:
    user.pin_hash = hash_pin(pin)
    user.pin_set_at = utcnow()
    clear_pin_state(user)
    user.pin_reset_token_hash = None
    user.pin_reset_token_expires_at = None


def set_reset_token(user, token: str, expires_at: datetime) -> None:
    user.pin_reset_token_hash = hash_pin_reset_token(token)
    user.pin_reset_token_expires_at = expires_at


def clear_reset_token(user) -> None:
    user.pin_reset_token_hash = None
    user.pin_reset_token_expires_at = None
