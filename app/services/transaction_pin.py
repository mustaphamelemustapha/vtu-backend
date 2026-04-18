from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from secrets import compare_digest

from app.core.config import get_settings
from app.core.security import hash_pin, verify_pin


settings = get_settings()

PIN_LENGTH = 4


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_pin(value: str | None) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits if len(digits) == PIN_LENGTH else ""


def hash_pin_reset_token(token: str) -> str:
    return sha256((token or "").encode("utf-8")).hexdigest()


def is_pin_locked(user) -> bool:
    locked_until = getattr(user, "pin_locked_until", None)
    if not locked_until:
        return False
    if locked_until.tzinfo is None:
        locked_until = locked_until.replace(tzinfo=timezone.utc)
    else:
        locked_until = locked_until.astimezone(timezone.utc)
    return locked_until > utcnow()


def clear_stale_lock(user) -> bool:
    locked_until = getattr(user, "pin_locked_until", None)
    if not locked_until:
        return False
    if locked_until.tzinfo is None:
        locked_until = locked_until.replace(tzinfo=timezone.utc)
    else:
        locked_until = locked_until.astimezone(timezone.utc)
    if locked_until <= utcnow():
        user.pin_locked_until = None
        user.pin_failed_attempts = 0
        return True
    return False


def clear_pin_state(user) -> None:
    user.pin_failed_attempts = 0
    user.pin_locked_until = None


def verify_pin_for_user(user, entered_pin: str) -> tuple[bool, str | None]:
    pin_hash_value = getattr(user, "pin_hash", None)
    if not pin_hash_value:
        return False, "Transaction PIN is not set"

    if clear_stale_lock(user):
        # Keep caller-side state in sync when the lock expired.
        pass

    if is_pin_locked(user):
        return False, "Transaction PIN is temporarily locked. Try again later."

    normalized = normalize_pin(entered_pin)
    if not normalized:
        return False, "PIN must be exactly 4 digits"

    if verify_pin(normalized, pin_hash_value):
        clear_pin_state(user)
        return True, None

    current_attempts = int(getattr(user, "pin_failed_attempts", 0) or 0) + 1
    max_attempts = max(3, int(settings.pin_max_failed_attempts))
    if current_attempts >= max_attempts:
        user.pin_failed_attempts = 0
        user.pin_locked_until = utcnow() + timedelta(minutes=int(settings.pin_lock_minutes))
        return False, "Transaction PIN is temporarily locked. Try again later."

    user.pin_failed_attempts = current_attempts
    remaining = max_attempts - current_attempts
    return False, f"Wrong PIN. {remaining} attempt{'s' if remaining != 1 else ''} left."


def pin_status_payload(user) -> dict:
    locked = is_pin_locked(user)
    locked_until = getattr(user, "pin_locked_until", None)
    if locked_until and locked_until.tzinfo is None:
        locked_until = locked_until.replace(tzinfo=timezone.utc)
    return {
        "is_set": bool(getattr(user, "pin_hash", None)),
        "is_locked": locked,
        "locked_until": locked_until,
        "failed_attempts": int(getattr(user, "pin_failed_attempts", 0) or 0) if not locked else 0,
        "max_attempts": max(3, int(settings.pin_max_failed_attempts)),
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
