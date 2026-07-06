from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import or_
from fastapi import HTTPException
from app.models import Wallet, WalletLedger, LedgerType, User, Transaction, TransactionType, TransactionStatus


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
    sender_name: str | None = None,
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

    # Atomically increment the wallet balance
    rows_updated = db.query(Wallet).filter(
        Wallet.id == wallet.id,
        Wallet.is_locked == False
    ).update(
        {Wallet.balance: Wallet.balance + amount},
        synchronize_session=False
    )
    if rows_updated == 0:
        db.refresh(wallet)
        if wallet.is_locked:
            raise HTTPException(status_code=423, detail="Wallet is locked")
        raise HTTPException(status_code=404, detail="Wallet not found or locked")

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
        db.refresh(wallet)
        try:
            if wallet.user and wallet.user.fcm_token:
                from app.services.push_notification import PushNotificationService
                if sender_name:
                    body_msg = f"Your wallet has been credited with ₦{amount:,.2f} from {sender_name.strip()}."
                else:
                    body_msg = f"Your wallet has been credited with ₦{amount:,.2f}. Ref: {reference}"
                PushNotificationService.send_to_token(
                    token=wallet.user.fcm_token,
                    title="Wallet Credited ₦" + f"{amount:,.2f}",
                    body=body_msg,
                    data={"type": "wallet", "reference": reference, "action": "credit"}
                )
        except Exception as push_exc:
            import logging
            logging.getLogger(__name__).warning("Failed to send credit wallet push notification: %s", push_exc)
    else:
        db.flush()
        db.refresh(wallet)
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

    # Atomically decrement the wallet balance only if it is sufficient
    rows_updated = db.query(Wallet).filter(
        Wallet.id == wallet.id,
        Wallet.balance >= amount,
        Wallet.is_locked == False
    ).update(
        {Wallet.balance: Wallet.balance - amount},
        synchronize_session=False
    )
    if rows_updated == 0:
        db.refresh(wallet)
        if wallet.is_locked:
            raise HTTPException(status_code=423, detail="Wallet is locked")
        raise HTTPException(status_code=400, detail="Insufficient balance")

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
    db.refresh(wallet)
    try:
        if wallet.user and wallet.user.fcm_token:
            from app.services.push_notification import PushNotificationService
            PushNotificationService.send_to_token(
                token=wallet.user.fcm_token,
                title="Wallet Debited ₦" + f"{amount:,.2f}",
                body=f"Your wallet has been debited with ₦{amount:,.2f}. Ref: {reference}",
                data={"type": "wallet", "reference": reference, "action": "debit"}
            )
    except Exception as push_exc:
        import logging
        logging.getLogger(__name__).warning("Failed to send debit wallet push notification: %s", push_exc)
    return entry

def verify_transfer_recipient(db: Session, identifier: str) -> User | None:
    return db.query(User).filter(
        or_(
            User.email == identifier,
            User.phone_number == identifier
        )
    ).first()

def execute_wallet_transfer(db: Session, sender: User, recipient: User, amount: Decimal) -> bool:
    sender_wallet = get_or_create_wallet(db, sender.id, commit=False)
    if sender_wallet.balance < amount:
        return False
        
    # Debit Sender
    sender_wallet.balance -= amount
    import uuid
    ref = f"TRF_{uuid.uuid4().hex[:12].upper()}"
    
    sender_ledger = WalletLedger(
        wallet_id=sender_wallet.id,
        amount=amount,
        entry_type=LedgerType.DEBIT,
        reference=ref,
        description=f"Transfer to {recipient.full_name}"
    )
    db.add(sender_ledger)
    
    sender_tx = Transaction(
        user_id=sender.id,
        reference=ref,
        amount=amount,
        status=TransactionStatus.SUCCESS,
        tx_type=TransactionType.WALLET_TRANSFER
    )
    db.add(sender_tx)
    
    # Credit Receiver
    receiver_wallet = get_or_create_wallet(db, recipient.id, commit=False)
    receiver_wallet.balance += amount
    
    receiver_ledger = WalletLedger(
        wallet_id=receiver_wallet.id,
        amount=amount,
        entry_type=LedgerType.CREDIT,
        reference=ref + "_RX",
        description=f"Received from {sender.full_name}"
    )
    db.add(receiver_ledger)
    
    receiver_tx = Transaction(
        user_id=recipient.id,
        reference=ref + "_RX",
        amount=amount,
        status=TransactionStatus.SUCCESS,
        tx_type=TransactionType.WALLET_FUND,
        provider="transfer"
    )
    db.add(receiver_tx)
    
    try:
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        # In a real app, log error
        raise e
