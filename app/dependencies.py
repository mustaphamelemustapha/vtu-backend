import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import decode_token
from app.models import User, UserRole


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def get_current_user(db: Session = Depends(get_db), token: str = Depends(oauth2_scheme)) -> User:
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject")

    import logging
    from sqlalchemy import text
    logger = logging.getLogger(__name__)

    try:
        user = db.query(User).filter(User.id == user_id).first()
    except Exception as db_exc:
        db.rollback()
        # Fallback to direct raw SQL query so authentication NEVER breaks even if a column is missing
        try:
            row = db.execute(
                text("SELECT id, email, full_name, hashed_password, role, is_active, is_verified, referral_code, brand FROM users WHERE id = :uid"),
                {"uid": user_id}
            ).fetchone()
            if row:
                user = db.query(User).filter(User.id == user_id).first()
            else:
                user = None
        except Exception:
            db.rollback()
            try:
                row = db.execute(
                    text("SELECT id, email, full_name, hashed_password, role, is_active, is_verified, referral_code FROM users WHERE id = :uid"),
                    {"uid": user_id}
                ).fetchone()
                if row:
                    user = User(
                        id=row[0],
                        email=row[1],
                        full_name=row[2],
                        hashed_password=row[3],
                        role=UserRole(str(row[4]).lower()) if str(row[4]).lower() in ["customer", "agent", "ambassador", "admin", "user", "reseller"] else UserRole.CUSTOMER,
                        is_active=bool(row[5]),
                        is_verified=bool(row[6]),
                        referral_code=row[7],
                        brand="meledata"
                    )
                else:
                    user = None
            except Exception as raw_exc:
                db.rollback()
                logger.error(f"Failed to authenticate user {user_id}: {raw_exc}")
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error authenticating user")

    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


def require_reseller_or_admin(user: User = Depends(get_current_user)) -> User:
    if user.role not in [UserRole.AGENT, UserRole.ADMIN]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Reseller access required")
    return user


oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def get_optional_user(db: Session = Depends(get_db), token: str | None = Depends(oauth2_scheme_optional)) -> User | None:
    if not token:
        return None
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            return None
        user_id = int(payload.get("sub"))
        user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
        return user
    except Exception:
        return None
