from decimal import Decimal
from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.models import Wallet, WalletLedger, LedgerType


def get_or_create_wallet(db: Session, user_id: int) -> Wallet:
    wallet = db.query(Wallet).filter(Wallet.user_id == user_id).first()
    if not wallet:
        wallet = Wallet(user_id=user_id, balance=0)
        db.add(wallet)
        db.commit()
        db.refresh(wallet)
    return wallet


def credit_wallet(db: Session, wallet: Wallet, amount: Decimal, reference: str, description: str) -> WalletLedger:
    if wallet.is_locked:
        raise HTTPException(status_code=423, detail="Wallet is locked")
    wallet.balance = Decimal(wallet.balance) + amount
    entry = WalletLedger(
        wallet_id=wallet.id,
        amount=amount,
        entry_type=LedgerType.CREDIT,
        reference=reference,
        description=description,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def debit_wallet(db: Session, wallet: Wallet, amount: Decimal, reference: str, description: str) -> WalletLedger:
    if wallet.is_locked:
        raise HTTPException(status_code=423, detail="Wallet is locked")
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
