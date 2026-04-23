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
    record_referral_data_activity,
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


def test_referral_progress_qualifies_once_at_50gb_and_rewards_once():
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

        first = record_referral_data_activity(
            db,
            user=referred,
            transaction_reference="TX-1",
            plan_size="25GB",
            transaction_status=TransactionStatus.SUCCESS.value,
        )
        second = record_referral_data_activity(
            db,
            user=referred,
            transaction_reference="TX-2",
            plan_size="25GB",
            transaction_status=TransactionStatus.SUCCESS.value,
        )
        repeat = record_referral_data_activity(
            db,
            user=referred,
            transaction_reference="TX-2",
            plan_size="25GB",
            transaction_status=TransactionStatus.SUCCESS.value,
        )
        db.commit()

        assert first is not None
        assert second is not None
        assert repeat is not None

        referral = db.query(Referral).filter_by(id=second.id).first()
        assert referral is not None
        assert referral.accumulated_mb == 51200
        assert referral.status.value == "rewarded"
        assert referral.reward_transaction_reference == f"REFERRAL_REWARD_{referral.id}"

        wallet = db.query(Wallet).filter(Wallet.user_id == referrer.id).first()
        assert wallet is not None
        assert Decimal(wallet.balance) == Decimal("2000.00")

        reward_txs = db.query(Transaction).filter(
            Transaction.user_id == referrer.id,
            Transaction.tx_type == TransactionType.WALLET_FUND,
            Transaction.reference == referral.reward_transaction_reference,
        ).all()
        assert len(reward_txs) == 1
    finally:
        db.close()


def test_refund_subtracts_progress_without_counting_twice():
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

        referral = record_referral_data_activity(
            db,
            user=referred,
            transaction_reference="TX-REFUND",
            plan_size="10GB",
            transaction_status=TransactionStatus.SUCCESS.value,
        )
        assert referral is not None
        assert referral.accumulated_mb == 10240

        refunded = record_referral_data_activity(
            db,
            user=referred,
            transaction_reference="TX-REFUND",
            plan_size="10GB",
            transaction_status=TransactionStatus.REFUNDED.value,
        )
        db.commit()

        assert refunded is not None
        assert refunded.accumulated_mb == 0
        assert refunded.status.value == "pending"
    finally:
        db.close()


def test_referral_dashboard_exposes_progress():
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
        record_referral_data_activity(
            db,
            user=referred,
            transaction_reference="TX-DASH",
            plan_size="1GB",
            transaction_status=TransactionStatus.SUCCESS.value,
        )

        dashboard = get_referral_dashboard(db, user=referrer)
        assert dashboard["referral_code"] == referrer.referral_code
        assert dashboard["total_referrals"] == 1
        assert dashboard["rewarded_referrals"] == 0
        assert dashboard["progress_percent"] == 2
        assert dashboard["referrals"][0]["progress_percent"] == 2
    finally:
        db.close()
