from datetime import datetime, timedelta
from typing import Optional
from hashlib import sha256
import jwt
from passlib.context import CryptContext
from app.core.config import get_settings


settings = get_settings()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")



def hash_password(password: str) -> str:
    return pwd_context.hash(password, rounds=settings.password_bcrypt_rounds)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def hash_pin(pin: str) -> str:
    return pwd_context.hash(pin, rounds=settings.pin_bcrypt_rounds)


def verify_pin(pin: str, hashed_pin: str) -> bool:
    return pwd_context.verify(pin, hashed_pin)


def hash_reset_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()



def create_access_token(subject: str, role: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": subject, "role": role, "type": "access", "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")



def create_refresh_token(subject: str, role: str) -> str:
    expire = datetime.utcnow() + timedelta(days=settings.refresh_token_expire_days)
    payload = {"sub": subject, "role": role, "type": "refresh", "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")



def decode_token(token: str) -> dict:
    keys = [settings.secret_key]
    legacy = getattr(settings, "legacy_secret_keys", "") or ""
    if legacy:
        keys.extend([k.strip() for k in legacy.split(",") if k.strip()])
    keys = list(dict.fromkeys([k for k in keys if k]))

    last_sig_err = None
    for key in keys:
        try:
            return jwt.decode(token, key, algorithms=["HS256"])
        except jwt.InvalidSignatureError as exc:
            last_sig_err = exc
            continue
        except Exception:
            raise

    if last_sig_err:
        raise last_sig_err
    return jwt.decode(token, settings.secret_key, algorithms=["HS256"])

