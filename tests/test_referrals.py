from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import User, UserRole, Wallet, Transaction, TransactionStatus, TransactionType, Referral
from app.services.referrals import (
    attach_signup_referral,
    ensure_user_referral_code,
    generate_referral_code,
    get_referral_dashboard,
    record_referral_first_deposit_reward,
)


ENGINE = create_engine(
    "sqlite+pysqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
SessionLocal = sessionmaker(bind=ENGINE, autocommit=False, autoflush=False)
Base.metadata.create_all(bind=ENGINE)


def _seed_user(db, *, email: str, full_name: str, referral_code: str | None = None, referred_by_id: int | None = None):
    user = User(
        email=email,
        full_name=full_name,
        hashed_password="hash",
        role=UserRole.USER,
        is_verified=True,
        referral_code=referral_code or generate_referral_code(1 if db.query(User).count() == 0 else db.query(User).count() + 1),
        referred_by_id=referred_by_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _fund_success(db, *, user: User, reference: str, amount: Decimal):
    tx = Transaction(
        user_id=user.id,
        reference=reference,
        amount=amount,
        status=TransactionStatus.SUCCESS,
        tx_type=TransactionType.WALLET_FUND,
    )
    db.add(tx)
    wallet = db.query(Wallet).filter(Wallet.user_id == user.id).first()
    if wallet is None:
        wallet = Wallet(user_id=user.id, balance=0)
        db.add(wallet)
    wallet.balance = Decimal(wallet.balance) + amount
    db.commit()
    db.refresh(tx)
    return tx


def test_attach_signup_referral_links_referred_user():
    db = SessionLocal()
    try:
        referrer = _seed_user(db, email="referrer@example.com", full_name="Referrer", referral_code="AX1")
        referred = _seed_user(db, email="new@example.com", full_name="New User", referral_code="AX2")
        row = attach_signup_referral(db, new_user=referred, referral_code=referrer.referral_code)
        db.commit()

        assert row is not None
        assert referred.referred_by_id == referrer.id
        assert row.referrer_id == referrer.id
        assert row.referred_user_id == referred.id
        assert row.referral_code_used == referrer.referral_code
        assert row.status.value == "pending"
    finally:
        db.close()


def test_new_user_gets_referral_code_before_flush():
    db = SessionLocal()
    try:
        user = User(
            email="fresh@example.com",
            full_name="Fresh User",
            hashed_password="hash",
            role=UserRole.USER,
            is_verified=True,
        )
        db.add(user)
        code = ensure_user_referral_code(db, user)
        assert code.startswith("AX")
        db.commit()
        db.refresh(user)
        assert user.referral_code == code
    finally:
        db.close()


def test_first_successful_deposit_rewards_once():
    db = SessionLocal()
    try:
        referrer = _seed_user(db, email="referrer2@example.com", full_name="Referrer 2", referral_code="AX10")
        referred = _seed_user(
            db,
            email="friend@example.com",
            full_name="Friend",
            referral_code="AX11",
            referred_by_id=referrer.id,
        )
        attach_signup_referral(db, new_user=referred, referral_code=referrer.referral_code)
        db.commit()

        tx = _fund_success(db, user=referred, reference="DEP-1", amount=Decimal("10000.00"))
        referral = record_referral_first_deposit_reward(
            db,
            user=referred,
            transaction_reference=tx.reference,
            deposit_amount=tx.amount,
            transaction_status=TransactionStatus.SUCCESS.value,
        )
        db.commit()

        assert referral is not None
        assert referral.first_deposit_amount == Decimal("10000.00")
        assert referral.reward_amount == Decimal("200.00")
        assert referral.status.value == "rewarded"
        assert referral.reward_transaction_reference == f"REFERRAL_DEPOSIT_REWARD_{referral.id}"

        wallet = db.query(Wallet).filter(Wallet.user_id == referrer.id).first()
        assert wallet is not None
        assert Decimal(wallet.balance) == Decimal("200.00")

        reward_txs = db.query(Transaction).filter(
            Transaction.user_id == referrer.id,
            Transaction.tx_type == TransactionType.WALLET_FUND,
            Transaction.reference == referral.reward_transaction_reference,
        ).all()
        assert len(reward_txs) == 1
    finally:
        db.close()


def test_second_deposit_does_not_duplicate_reward():
    db = SessionLocal()
    try:
        referrer = _seed_user(db, email="referrer3@example.com", full_name="Referrer 3", referral_code="AX20")
        referred = _seed_user(
            db,
            email="buyer@example.com",
            full_name="Buyer",
            referral_code="AX21",
            referred_by_id=referrer.id,
        )
        attach_signup_referral(db, new_user=referred, referral_code=referrer.referral_code)
        db.commit()

        first_tx = _fund_success(db, user=referred, reference="DEP-2", amount=Decimal("5000.00"))
        record_referral_first_deposit_reward(
            db,
            user=referred,
            transaction_reference=first_tx.reference,
            deposit_amount=first_tx.amount,
            transaction_status=TransactionStatus.SUCCESS.value,
        )
        db.commit()

        second_tx = _fund_success(db, user=referred, reference="DEP-3", amount=Decimal("12000.00"))
        second = record_referral_first_deposit_reward(
            db,
            user=referred,
            transaction_reference=second_tx.reference,
            deposit_amount=second_tx.amount,
            transaction_status=TransactionStatus.SUCCESS.value,
        )
        db.commit()

        assert second is not None
        referral = db.query(Referral).filter_by(id=second.id).first()
        assert referral is not None
        assert referral.first_deposit_amount == Decimal("5000.00")
        assert referral.reward_amount == Decimal("100.00")
        wallet = db.query(Wallet).filter(Wallet.user_id == referrer.id).first()
        assert wallet is not None
        assert Decimal(wallet.balance) == Decimal("100.00")
        reward_txs = db.query(Transaction).filter(
            Transaction.user_id == referrer.id,
            Transaction.tx_type == TransactionType.WALLET_FUND,
        ).all()
        assert len(reward_txs) == 1
    finally:
        db.close()


def test_duplicate_webhook_for_first_deposit_rewards_once():
    db = SessionLocal()
    try:
        referrer = _seed_user(db, email="referrer5@example.com", full_name="Referrer 5", referral_code="AX40")
        referred = _seed_user(
            db,
            email="dup@example.com",
            full_name="Duplicate",
            referral_code="AX41",
            referred_by_id=referrer.id,
        )
        attach_signup_referral(db, new_user=referred, referral_code=referrer.referral_code)
        db.commit()

        tx = _fund_success(db, user=referred, reference="DEP-DUP", amount=Decimal("15000.00"))
        first = record_referral_first_deposit_reward(
            db,
            user=referred,
            transaction_reference=tx.reference,
            deposit_amount=tx.amount,
            transaction_status=TransactionStatus.SUCCESS.value,
        )
        second = record_referral_first_deposit_reward(
            db,
            user=referred,
            transaction_reference=tx.reference,
            deposit_amount=tx.amount,
            transaction_status=TransactionStatus.SUCCESS.value,
        )
        db.commit()

        assert first is not None
        assert second is not None
        referral = db.query(Referral).filter_by(id=first.id).first()
        assert referral is not None
        assert referral.reward_amount == Decimal("300.00")
        wallet = db.query(Wallet).filter(Wallet.user_id == referrer.id).first()
        assert wallet is not None
        assert Decimal(wallet.balance) == Decimal("300.00")
        reward_txs = db.query(Transaction).filter(
            Transaction.user_id == referrer.id,
            Transaction.tx_type == TransactionType.WALLET_FUND,
        ).all()
        assert len(reward_txs) == 1
    finally:
        db.close()


def test_referral_dashboard_exposes_first_deposit_and_total_earned():
    db = SessionLocal()
    try:
        referrer = _seed_user(db, email="referrer4@example.com", full_name="Referrer 4", referral_code="AX30")
        referred = _seed_user(
            db,
            email="invitee@example.com",
            full_name="Invitee",
            referral_code="AX31",
            referred_by_id=referrer.id,
        )
        attach_signup_referral(db, new_user=referred, referral_code=referrer.referral_code)
        db.commit()
        tx = _fund_success(db, user=referred, reference="DEP-4", amount=Decimal("25000.00"))
        record_referral_first_deposit_reward(
            db,
            user=referred,
            transaction_reference=tx.reference,
            deposit_amount=tx.amount,
            transaction_status=TransactionStatus.SUCCESS.value,
        )
        db.commit()

        dashboard = get_referral_dashboard(db, user=referrer)
        assert dashboard["referral_code"] == referrer.referral_code
        assert dashboard["total_referrals"] == 1
        assert dashboard["rewarded_referrals"] == 1
        assert dashboard["total_earned"] == Decimal("500.00")
        assert dashboard["referrals"][0]["first_deposit_amount"] == Decimal("25000.00")
        assert dashboard["referrals"][0]["reward_amount"] == Decimal("500.00")
    finally:
        db.close()
