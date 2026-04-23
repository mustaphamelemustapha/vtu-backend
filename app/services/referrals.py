from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import secrets

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Referral, ReferralContribution, ReferralStatus, Transaction, TransactionStatus, TransactionType, User
from app.services.wallet import credit_wallet, get_or_create_wallet

settings = get_settings()

DEFAULT_TARGET_MB = 51200
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


def _parse_size_mb(value: str | None) -> int:
    text = str(value or "").strip().upper()
    if not text:
        return 0
    import re

    match = re.search(r"(\d+(?:\.\d+)?)\s*(GB|MB)", text, re.IGNORECASE)
    if not match:
        return 0
    amount = float(match.group(1))
    unit = match.group(2).upper()
    mb = amount * 1024 if unit == "GB" else amount
    return max(0, int(round(mb)))


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
        accumulated_mb=0,
        target_mb=DEFAULT_TARGET_MB,
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
        accumulated_mb=0,
        target_mb=DEFAULT_TARGET_MB,
        reward_amount=DEFAULT_REWARD_AMOUNT,
        status=ReferralStatus.PENDING,
    )
    db.add(referral)
    db.flush()
    return referral


def _grant_reward(db: Session, *, referral: Referral, referrer: User, purchaser_name: str | None, source_reference: str) -> str:
    reward_reference = referral.reward_transaction_reference or f"REFERRAL_REWARD_{referral.id}"
    wallet = get_or_create_wallet(db, referrer.id, commit=False)
    reward_amount = _safe_reward_amount(referral.reward_amount)
    description = (
        f"Referral reward from {purchaser_name.strip()}"
        if purchaser_name and purchaser_name.strip()
        else "Referral reward"
    )

    existing_tx = db.query(Transaction).filter(Transaction.reference == reward_reference).first()
    if existing_tx:
        referral.reward_transaction_reference = reward_reference
        referral.rewarded_at = referral.rewarded_at or _utcnow()
        referral.status = ReferralStatus.REWARDED
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
    if not referral.qualifying_transaction_reference:
        referral.qualifying_transaction_reference = source_reference
    db.flush()
    return reward_reference


def record_referral_data_activity(
    db: Session,
    *,
    user: User,
    transaction_reference: str,
    plan_size: str | None,
    transaction_status: str,
) -> Referral | None:
    referral = _get_or_create_referral_row(db, referred_user=user)
    if not referral:
        return None

    reference = str(transaction_reference or "").strip()
    if not reference:
        return referral

    status = str(transaction_status or "").strip().lower()
    mb = _parse_size_mb(plan_size)
    if mb <= 0:
        return referral

    contribution = db.query(ReferralContribution).filter(ReferralContribution.transaction_reference == reference).first()

    if status == TransactionStatus.REFUNDED.value:
        if contribution and contribution.reversed_at is None:
            referral.accumulated_mb = max(0, int(referral.accumulated_mb or 0) - int(contribution.mb or 0))
            contribution.reversed_at = _utcnow()
            if referral.status != ReferralStatus.REWARDED:
                referral.status = (
                    ReferralStatus.QUALIFIED
                    if int(referral.accumulated_mb or 0) >= int(referral.target_mb or DEFAULT_TARGET_MB)
                    else ReferralStatus.PENDING
                )
            db.commit()
        return referral

    if status not in {TransactionStatus.SUCCESS.value, TransactionStatus.REFUNDED.value}:
        return referral

    if contribution:
        return referral

    contribution = ReferralContribution(
        referral_id=referral.id,
        transaction_reference=reference,
        mb=mb,
    )
    db.add(contribution)
    referral.accumulated_mb = int(referral.accumulated_mb or 0) + mb
    if referral.qualified_at is None and int(referral.accumulated_mb or 0) >= int(referral.target_mb or DEFAULT_TARGET_MB):
        referral.qualified_at = _utcnow()
        referral.status = ReferralStatus.QUALIFIED
        referrer = db.query(User).filter(User.id == referral.referrer_id).first()
        if referrer:
            _grant_reward(
                db,
                referral=referral,
                referrer=referrer,
                purchaser_name=(user.full_name or user.email or "").strip(),
                source_reference=reference,
            )
    db.commit()
    return referral


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
    total_accumulated_mb = sum(int(item.accumulated_mb or 0) for item in referrals)
    target_mb = int(referrals[0].target_mb if referrals else DEFAULT_TARGET_MB)
    reward_amount = _safe_reward_amount(referrals[0].reward_amount if referrals else DEFAULT_REWARD_AMOUNT)
    referral_link = None
    if settings.frontend_base_url:
        base = str(settings.frontend_base_url).rstrip("/")
        referral_link = f"{base}/register?ref={code}" if code else None

    items = []
    for item in referrals:
        accumulated_mb = int(item.accumulated_mb or 0)
        item_target_mb = int(item.target_mb or DEFAULT_TARGET_MB)
        progress = 0
        if item_target_mb > 0:
            progress = min(100, int(round((accumulated_mb / item_target_mb) * 100)))
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
                "accumulated_mb": accumulated_mb,
                "target_mb": item_target_mb,
                "progress_percent": progress,
                "reward_amount": Decimal(str(item.reward_amount or reward_amount)),
                "qualified_at": item.qualified_at,
                "rewarded_at": item.rewarded_at,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
            }
        )

    overall_progress = 0
    if items:
        overall_target = sum(int(item["target_mb"]) for item in items)
        if overall_target > 0:
            overall_progress = min(100, int(round((total_accumulated_mb / overall_target) * 100)))

    return {
        "referral_code": code,
        "referral_link": referral_link,
        "total_referrals": len(referrals),
        "rewarded_referrals": len(rewarded),
        "total_earned": total_earned,
        "total_accumulated_mb": total_accumulated_mb,
        "target_mb": target_mb,
        "reward_amount": reward_amount,
        "progress_percent": overall_progress,
        "referrals": items,
    }
