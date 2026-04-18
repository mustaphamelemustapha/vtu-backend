from __future__ import annotations

import logging
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi import Body
from app.core.config import get_settings
from app.core.database import get_db
from app.dependencies import get_current_user
from app.middlewares.rate_limit import limiter
from app.schemas.security import MessageResponse, PinStatusResponse
from app.services.email import send_transaction_pin_reset_email
from app.services.transaction_pin import (
    clear_reset_token,
    clear_stale_lock,
    pin_status_payload,
    set_pin,
    set_reset_token,
    validate_reset_token,
    verify_pin_for_user,
)


settings = get_settings()
router = APIRouter()
logger = logging.getLogger(__name__)


def _utcnow():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _pin_exists(user) -> bool:
    return bool(getattr(user, "pin_hash", None))


def _refresh_lock_if_needed(user, db) -> None:
    if clear_stale_lock(user):
        db.commit()
        db.refresh(user)


@router.get("/pin/status", response_model=PinStatusResponse)
@limiter.limit("30/minute")
def pin_status(
    request: Request,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    _refresh_lock_if_needed(user, db)
    return PinStatusResponse(**pin_status_payload(user))


@router.post("/pin/setup", response_model=MessageResponse)
@limiter.limit("5/minute")
def pin_setup(
    request: Request,
    payload: dict = Body(...),
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    _refresh_lock_if_needed(user, db)
    if _pin_exists(user):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Transaction PIN is already set")
    pin = "".join(ch for ch in str(payload.get("pin") or "") if ch.isdigit())
    confirm_pin = "".join(ch for ch in str(payload.get("confirm_pin") or "") if ch.isdigit())
    if len(pin) != 4 or len(confirm_pin) != 4:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="PIN must be exactly 4 digits")
    if pin != confirm_pin:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="PIN mismatch. Please try again.")

    set_pin(user, pin)
    db.commit()
    return MessageResponse(message="Transaction PIN created successfully")


@router.post("/pin/verify", response_model=MessageResponse)
@limiter.limit("10/minute")
def pin_verify(
    request: Request,
    payload: dict = Body(...),
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    _refresh_lock_if_needed(user, db)
    pin = "".join(ch for ch in str(payload.get("pin") or "") if ch.isdigit())
    ok, message = verify_pin_for_user(user, pin)
    if not ok:
        db.commit()
        if message and "locked" in message.lower():
            raise HTTPException(status_code=status.HTTP_423_LOCKED, detail=message)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message or "Wrong PIN. Please try again.")

    db.commit()
    return MessageResponse(message="Transaction PIN verified")


@router.post("/pin/change", response_model=MessageResponse)
@limiter.limit("5/minute")
def pin_change(
    request: Request,
    payload: dict = Body(...),
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    _refresh_lock_if_needed(user, db)
    if not _pin_exists(user):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Transaction PIN is not set")
    current_pin = "".join(ch for ch in str(payload.get("current_pin") or "") if ch.isdigit())
    new_pin = "".join(ch for ch in str(payload.get("new_pin") or "") if ch.isdigit())
    confirm_pin = "".join(ch for ch in str(payload.get("confirm_pin") or "") if ch.isdigit())
    if len(current_pin) != 4 or len(new_pin) != 4 or len(confirm_pin) != 4:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="PIN must be exactly 4 digits")
    if new_pin != confirm_pin:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="PIN mismatch. Please try again.")

    ok, message = verify_pin_for_user(user, current_pin)
    if not ok:
        db.commit()
        if message and "locked" in message.lower():
            raise HTTPException(status_code=status.HTTP_423_LOCKED, detail=message)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message or "Current PIN is incorrect")

    set_pin(user, new_pin)
    db.commit()
    return MessageResponse(message="Transaction PIN updated successfully")


@router.post("/pin/reset-request", response_model=MessageResponse)
@limiter.limit("3/minute")
def pin_reset_request(
    request: Request,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    _refresh_lock_if_needed(user, db)
    if not _pin_exists(user):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Transaction PIN is not set")
    token = secrets.token_urlsafe(32)
    expires_at = _utcnow() + timedelta(minutes=int(settings.pin_reset_token_minutes))
    set_reset_token(user, token, expires_at)
    db.commit()

    try:
        send_transaction_pin_reset_email(user.email, token)
    except Exception as exc:
        logger.warning(
            "Transaction PIN reset email failed to=%s provider=%s error=%s",
            user.email,
            settings.email_provider,
            exc,
        )

    return MessageResponse(message="Reset link sent to your email")


@router.post("/pin/reset-confirm", response_model=MessageResponse)
@limiter.limit("5/minute")
def pin_reset_confirm(
    request: Request,
    payload: dict = Body(...),
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    _refresh_lock_if_needed(user, db)
    token = (payload.get("token") or "").strip()
    new_pin = "".join(ch for ch in str(payload.get("new_pin") or "") if ch.isdigit())
    confirm_pin = "".join(ch for ch in str(payload.get("confirm_pin") or "") if ch.isdigit())
    if len(new_pin) != 4 or len(confirm_pin) != 4:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="PIN must be exactly 4 digits")
    if new_pin != confirm_pin:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="PIN mismatch. Please try again.")
    if not token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset token")
    expires_at = getattr(user, "pin_reset_token_expires_at", None)
    if not expires_at:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset token")

    if expires_at.tzinfo is None:
        from datetime import timezone

        expires_at = expires_at.replace(tzinfo=timezone.utc)
    else:
        from datetime import timezone

        expires_at = expires_at.astimezone(timezone.utc)
    if expires_at < _utcnow():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset token")

    if not validate_reset_token(user, token):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset token")

    set_pin(user, new_pin)
    clear_reset_token(user)
    db.commit()
    return MessageResponse(message="Transaction PIN reset successfully")
