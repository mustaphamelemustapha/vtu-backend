from decimal import Decimal
from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.models import Wallet, WalletLedger, LedgerType


def _find_matching_ledger(db: Session, *, wallet: Wallet, amount: Decimal, reference: str, description: str, entry_type: LedgerType) -> WalletLedger | None:
    return (
        db.query(WalletLedger)
        .filter(
            WalletLedger.wallet_id == wallet.id,
            WalletLedger.amount == amount,
            WalletLedger.reference == reference,
            WalletLedger.description == description,
            WalletLedger.entry_type == entry_type,
        )
        .order_by(WalletLedger.id.desc())
        .first()
    )


def get_or_create_wallet(db: Session, user_id: int, *, commit: bool = True) -> Wallet:
    wallet = db.query(Wallet).filter(Wallet.user_id == user_id).first()
    if not wallet:
        wallet = Wallet(user_id=user_id, balance=0)
        db.add(wallet)
        if commit:
            db.commit()
            db.refresh(wallet)
        else:
            db.flush()
    return wallet


def credit_wallet(
    db: Session,
    wallet: Wallet,
    amount: Decimal,
    reference: str,
    description: str,
    *,
    commit: bool = True,
) -> WalletLedger:
    if wallet.is_locked:
        raise HTTPException(status_code=423, detail="Wallet is locked")
    existing = _find_matching_ledger(
        db,
        wallet=wallet,
        amount=amount,
        reference=reference,
        description=description,
        entry_type=LedgerType.CREDIT,
    )
    if existing:
        return existing
    wallet.balance = Decimal(wallet.balance) + amount
    entry = WalletLedger(
        wallet_id=wallet.id,
        amount=amount,
        entry_type=LedgerType.CREDIT,
        reference=reference,
        description=description,
    )
    db.add(entry)
    if commit:
        db.commit()
        db.refresh(entry)
    else:
        db.flush()
    return entry


def debit_wallet(db: Session, wallet: Wallet, amount: Decimal, reference: str, description: str) -> WalletLedger:
    if wallet.is_locked:
        raise HTTPException(status_code=423, detail="Wallet is locked")
    existing = _find_matching_ledger(
        db,
        wallet=wallet,
        amount=amount,
        reference=reference,
        description=description,
        entry_type=LedgerType.DEBIT,
    )
    if existing:
        return existing
    if Decimal(wallet.balance) < amount:
        raise HTTPException(status_code=400, detail="Insufficient balance")
    wallet.balance = Decimal(wallet.balance) - amount
    entry = WalletLedger(
        wallet_id=wallet.id,
        amount=amount,
        entry_type=LedgerType.DEBIT,
        reference=reference,
        description=description,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry
