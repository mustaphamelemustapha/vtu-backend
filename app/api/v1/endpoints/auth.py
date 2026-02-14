from datetime import datetime, timedelta, timezone
import logging
import secrets
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from app.core.security import hash_password, verify_password, create_access_token, create_refresh_token, decode_token
from app.core.config import get_settings
from app.core.database import get_db
from app.middlewares.rate_limit import limiter
from app.models import User, UserRole
from app.schemas.auth import RegisterRequest, LoginRequest, TokenPair, RefreshRequest, Message, ForgotPasswordRequest, ForgotPasswordResponse, ResetPasswordRequest, ChangePasswordRequest, UpdateMeRequest, EmailVerification
from app.schemas.user import UserOut
from app.dependencies import get_current_user
from app.services.wallet import get_or_create_wallet
from app.services.email import send_password_reset_email

settings = get_settings()
router = APIRouter()
logger = logging.getLogger(__name__)

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    # DB might return aware (preferred) or naive timestamps depending on driver/config.
    # Treat naive timestamps as UTC to avoid 500s in comparisons.
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _mask_email(value: str) -> str:
    try:
        local, domain = value.split("@", 1)
    except ValueError:
        return "***"
    if not local:
        return f"***@{domain}"
    return f"{local[:2]}***@{domain}"


@router.post("/register", response_model=UserOut)
@limiter.limit("10/minute")
def register(request: Request, payload: RegisterRequest, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        email=payload.email,
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
        role=UserRole.USER,
        is_verified=False,
    )
    user.verification_token = secrets.token_urlsafe(32)
    user.verification_token_expires_at = _utcnow() + timedelta(days=2)

    db.add(user)
    db.commit()
    db.refresh(user)

    get_or_create_wallet(db, user.id)
    return user


@router.post("/login", response_model=TokenPair)
@limiter.limit("10/minute")
def login(request: Request, payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="User is inactive")

    return TokenPair(
        access_token=create_access_token(str(user.id), user.role.value),
        refresh_token=create_refresh_token(str(user.id), user.role.value),
    )


@router.post("/refresh", response_model=TokenPair)
@limiter.limit("30/minute")
def refresh(request: Request, payload: RefreshRequest, db: Session = Depends(get_db)):
    try:
        decoded = decode_token(payload.refresh_token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    if decoded.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    try:
        user_id = int(decoded.get("sub"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    return TokenPair(
        access_token=create_access_token(str(user.id), user.role.value),
        refresh_token=create_refresh_token(str(user.id), user.role.value),
    )


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
@limiter.limit("5/minute")
def forgot_password(request: Request, payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    reset_token = None
    if user:
        reset_token = secrets.token_urlsafe(32)
        user.reset_token = reset_token
        user.reset_token_expires_at = _utcnow() + timedelta(hours=2)
        db.commit()
        # Send email only when the user exists. Response stays generic either way.
        try:
            send_password_reset_email(user.email, reset_token)
        except Exception as exc:
            # Avoid leaking provider failures in the API response; log for ops/debugging.
            logger.warning(
                "Password reset email send failed to=%s provider=%s error=%s",
                _mask_email(user.email),
                settings.email_provider,
                exc,
            )

    # Avoid user enumeration: always return the same message.
    message = "If the email exists, a reset token has been generated"
    env = (settings.environment or "").lower()
    if env and env != "production" and reset_token:
        return ForgotPasswordResponse(message=message, reset_token=reset_token)
    return ForgotPasswordResponse(message=message)


@router.post("/reset-password", response_model=Message)
@limiter.limit("10/minute")
def reset_password(request: Request, payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.reset_token == payload.token).first()
    if not user or not user.reset_token_expires_at:
        raise HTTPException(status_code=400, detail="Invalid or expired token")
    if _as_utc(user.reset_token_expires_at) < _utcnow():
        raise HTTPException(status_code=400, detail="Invalid or expired token")

    user.hashed_password = hash_password(payload.new_password)
    user.reset_token = None
    user.reset_token_expires_at = None
    db.commit()

    return Message(message="Password reset successful")


@router.post("/verify-email", response_model=Message)
@limiter.limit("10/minute")
def verify_email(request: Request, payload: EmailVerification, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.verification_token == payload.token).first()
    if not user or not user.verification_token_expires_at:
        raise HTTPException(status_code=400, detail="Invalid or expired token")
    if _as_utc(user.verification_token_expires_at) < _utcnow():
        raise HTTPException(status_code=400, detail="Invalid or expired token")

    user.is_verified = True
    user.verification_token = None
    user.verification_token_expires_at = None
    db.commit()

    return Message(message="Email verified successfully")


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.patch("/me", response_model=UserOut)
def update_me(payload: UpdateMeRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    full_name = (payload.full_name or "").strip()
    if len(full_name) < 2:
        raise HTTPException(status_code=400, detail="Full name is too short")
    if len(full_name) > 255:
        raise HTTPException(status_code=400, detail="Full name is too long")

    user.full_name = full_name
    db.commit()
    db.refresh(user)
    return user


@router.post("/change-password", response_model=Message)
@limiter.limit("5/minute")
def change_password(
    request: Request,
    payload: ChangePasswordRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not verify_password(payload.current_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    user.hashed_password = hash_password(payload.new_password)
    # Invalidate any outstanding reset tokens.
    user.reset_token = None
    user.reset_token_expires_at = None
    db.commit()
    return Message(message="Password updated successfully")
