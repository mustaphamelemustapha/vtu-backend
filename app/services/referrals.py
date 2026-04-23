from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import secrets

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Referral, ReferralStatus, Transaction, TransactionStatus, TransactionType, User
from app.services.wallet import credit_wallet, get_or_create_wallet

settings = get_settings()

DEFAULT_REWARD_AMOUNT = Decimal("2000.00")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _base36(value: int) -> str:
    digits = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if value <= 0:
        return "0"
    result = []
    number = int(value)
    while number:
        number, rem = divmod(number, 36)
        result.append(digits[rem])
    return "".join(reversed(result))


def normalize_referral_code(value: str | None) -> str:
    return str(value or "").strip().upper().replace(" ", "")


_REFERRAL_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def _random_suffix(length: int = 7) -> str:
    return "".join(secrets.choice(_REFERRAL_ALPHABET) for _ in range(length))


def generate_referral_code(user_id: int | None = None) -> str:
    if user_id is not None:
        return f"AX{_base36(user_id)}"
    return f"AX{_random_suffix()}"


def ensure_user_referral_code(db: Session, user: User) -> str:
    code = normalize_referral_code(getattr(user, "referral_code", None))
    if code:
        return code
    if getattr(user, "id", None):
        code = generate_referral_code(int(user.id))
    else:
        code = generate_referral_code(None)
    while db.query(User).filter(User.referral_code == code).first():
        code = generate_referral_code(None)
    user.referral_code = code
    if getattr(user, "id", None):
        db.flush()
    return code


def _safe_reward_amount(value) -> Decimal:
    try:
        return Decimal(str(value or DEFAULT_REWARD_AMOUNT))
    except Exception:
        return DEFAULT_REWARD_AMOUNT


def _safe_decimal_amount(value) -> Decimal:
    try:
        amount = Decimal(str(value))
    except Exception:
        amount = Decimal("0")
    return amount if amount > 0 else Decimal("0")


def attach_signup_referral(db: Session, *, new_user: User, referral_code: str | None) -> Referral | None:
    code = normalize_referral_code(referral_code)
    if not code:
        return None

    referrer = db.query(User).filter(User.referral_code == code).first()
    if not referrer:
        db.rollback()
        raise HTTPException(status_code=400, detail="Invalid referral code")
    if referrer.id == new_user.id:
        db.rollback()
        raise HTTPException(status_code=400, detail="Self-referral is not allowed")
    if getattr(new_user, "referred_by_id", None) and int(new_user.referred_by_id) != referrer.id:
        db.rollback()
        raise HTTPException(status_code=400, detail="Referral code already attached")

    existing = db.query(Referral).filter(Referral.referred_user_id == new_user.id).first()
    if existing:
        return existing

    new_user.referred_by_id = referrer.id
    referral = Referral(
        referrer_id=referrer.id,
        referred_user_id=new_user.id,
        referral_code_used=code,
        reward_amount=DEFAULT_REWARD_AMOUNT,
        status=ReferralStatus.PENDING,
    )
    db.add(referral)
    db.flush()
    return referral


def _get_or_create_referral_row(db: Session, *, referred_user: User) -> Referral | None:
    if not getattr(referred_user, "referred_by_id", None):
        return None
    referral = db.query(Referral).filter(Referral.referred_user_id == referred_user.id).first()
    if referral:
        return referral
    referrer = db.query(User).filter(User.id == referred_user.referred_by_id).first()
    if not referrer:
        return None
    referral = Referral(
        referrer_id=referrer.id,
        referred_user_id=referred_user.id,
        referral_code_used=normalize_referral_code(referrer.referral_code),
        reward_amount=DEFAULT_REWARD_AMOUNT,
        status=ReferralStatus.PENDING,
    )
    db.add(referral)
    db.flush()
    return referral


def _grant_reward(
    db: Session,
    *,
    referral: Referral,
    referrer: User,
    first_deposit_amount: Decimal,
    reward_amount: Decimal,
    source_reference: str,
) -> str:
    reward_reference = referral.reward_transaction_reference or f"REFERRAL_DEPOSIT_REWARD_{referral.id}"
    wallet = get_or_create_wallet(db, referrer.id, commit=False)
    description = f"Referral reward from first deposit of ₦{first_deposit_amount:.2f}"

    existing_tx = db.query(Transaction).filter(Transaction.reference == reward_reference).first()
    if existing_tx:
        referral.reward_transaction_reference = reward_reference
        referral.rewarded_at = referral.rewarded_at or _utcnow()
        referral.status = ReferralStatus.REWARDED
        referral.first_deposit_amount = first_deposit_amount
        referral.reward_amount = reward_amount
        if not referral.qualifying_transaction_reference:
            referral.qualifying_transaction_reference = source_reference
        db.flush()
        return reward_reference

    # Reuse the wallet helper but keep the outer transaction in control.
    credit_wallet(db, wallet, reward_amount, reward_reference, description, commit=False)
    tx = Transaction(
        user_id=referrer.id,
        reference=reward_reference,
        amount=reward_amount,
        status=TransactionStatus.SUCCESS,
        tx_type=TransactionType.WALLET_FUND,
        external_reference=source_reference,
    )
    db.add(tx)
    referral.reward_transaction_reference = reward_reference
    referral.rewarded_at = _utcnow()
    referral.status = ReferralStatus.REWARDED
    referral.first_deposit_amount = first_deposit_amount
    referral.reward_amount = reward_amount
    if not referral.qualifying_transaction_reference:
        referral.qualifying_transaction_reference = source_reference
    db.flush()
    return reward_reference


def record_referral_first_deposit_reward(
    db: Session,
    *,
    user: User,
    transaction_reference: str,
    deposit_amount: Decimal | int | float | str,
    transaction_status: str,
) -> Referral | None:
    referral = _get_or_create_referral_row(db, referred_user=user)
    if not referral:
        return None

    reference = str(transaction_reference or "").strip()
    if not reference:
        return referral

    status = str(transaction_status or "").strip().lower()
    if status != TransactionStatus.SUCCESS.value:
        return referral

    referral = (
        db.query(Referral)
        .filter(Referral.id == referral.id)
        .with_for_update()
        .first()
        or referral
    )

    if referral.reward_transaction_reference or referral.rewarded_at:
        return referral

    first_success = (
        db.query(Transaction)
        .filter(
            Transaction.user_id == user.id,
            Transaction.tx_type == TransactionType.WALLET_FUND,
            Transaction.status == TransactionStatus.SUCCESS,
        )
        .order_by(Transaction.created_at.asc(), Transaction.id.asc())
        .first()
    )
    if not first_success or first_success.reference != reference:
        return referral

    amount = _safe_decimal_amount(deposit_amount)
    if amount <= 0:
        return referral

    reward_amount = (amount * Decimal("0.02")).quantize(Decimal("0.01"))
    if reward_amount <= 0:
        return referral

    referral.first_deposit_amount = amount
    referral.reward_amount = reward_amount
    referral.qualifying_transaction_reference = reference
    referral.qualified_at = referral.qualified_at or _utcnow()
    referral.status = ReferralStatus.QUALIFIED
    referrer = db.query(User).filter(User.id == referral.referrer_id).first()
    if referrer:
        _grant_reward(
            db,
            referral=referral,
            referrer=referrer,
            first_deposit_amount=amount,
            reward_amount=reward_amount,
            source_reference=reference,
        )
    db.commit()
    return referral


def record_referral_data_activity(*args, **kwargs) -> Referral | None:  # Legacy shim
    return None


def get_referral_dashboard(db: Session, *, user: User) -> dict:
    code = normalize_referral_code(user.referral_code)
    if not code and getattr(user, "id", None):
        code = ensure_user_referral_code(db, user)
    referrals = (
        db.query(Referral)
        .filter(Referral.referrer_id == user.id)
        .order_by(Referral.created_at.desc(), Referral.id.desc())
        .all()
    )
    rewarded = [item for item in referrals if item.status == ReferralStatus.REWARDED]
    total_earned = sum((Decimal(str(item.reward_amount or 0)) for item in rewarded), Decimal("0"))
    reward_amount = _safe_reward_amount(referrals[0].reward_amount if referrals else Decimal("0"))
    referral_link = None
    if settings.frontend_base_url:
        base = str(settings.frontend_base_url).rstrip("/")
        referral_link = f"{base}/register?ref={code}" if code else None

    items = []
    for item in referrals:
        referred_user = item.referred_user
        referred_user_name = (
            referred_user.full_name
            if referred_user and getattr(referred_user, "full_name", None)
            else (referred_user.email if referred_user and getattr(referred_user, "email", None) else "")
        )
        items.append(
            {
                "id": item.id,
                "referred_user_name": referred_user_name,
                "referral_code_used": item.referral_code_used,
                "status": item.status.value if hasattr(item.status, "value") else str(item.status),
                "first_deposit_amount": Decimal(str(item.first_deposit_amount or 0)),
                "reward_amount": Decimal(str(item.reward_amount or reward_amount)),
                "qualified_at": item.qualified_at,
                "rewarded_at": item.rewarded_at,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
            }
        )

    return {
        "referral_code": code,
        "referral_link": referral_link,
        "total_referrals": len(referrals),
        "rewarded_referrals": len(rewarded),
        "total_earned": total_earned,
        "reward_amount": reward_amount,
        "referrals": items,
    }
