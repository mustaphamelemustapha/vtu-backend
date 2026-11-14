import uuid
import pytest
from decimal import Decimal
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import User, UserRole, Wallet, Transaction, TransactionStatus, TransactionType, WalletLedger
from app.services.commissions import (
    has_user_funded_wallet,
    get_user_total_data_gb,
    check_and_award_welcome_data_bonus,
    get_brand_commission_config,
    reconcile_missing_welcome_bonuses,
)

ENGINE = create_engine(
    "sqlite+pysqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
SessionLocal = sessionmaker(bind=ENGINE, autocommit=False, autoflush=False)
Base.metadata.create_all(bind=ENGINE)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def create_user(db, email_prefix="user", brand="kullomadata"):
    uid = uuid.uuid4().hex[:8]
    u = User(
        email=f"{email_prefix}_{uid}@example.com",
        phone_number=f"080{uid[:8]}",
        full_name=f"User {uid}",
        hashed_password="dummy_hash_for_testing",
        referral_code=f"REF_{uid}",
        role=UserRole.CUSTOMER,
        is_active=True,
        brand=brand,
    )
    db.add(u)
    db.commit()
    db.refresh(u)

    w = Wallet(user_id=u.id, balance=Decimal("1000.00"))
    db.add(w)
    db.commit()
    return u


def test_kullomadata_brand_defaults(db):
    cfg = get_brand_commission_config(db, brand="kullomadata")
    assert cfg["brand"] == "kullomadata"
    assert cfg["enable_welcome_data_bonus"] is True
    assert cfg["welcome_data_bonus_amount"] == 300.0
    assert cfg["welcome_data_bonus_min_gb"] == 2.0


def test_has_user_funded_wallet_false_when_no_deposits(db):
    u = create_user(db, "nofund")
    assert has_user_funded_wallet(db, u.id) is False


def test_has_user_funded_wallet_true_after_wallet_fund(db):
    u = create_user(db, "funded")
    tx = Transaction(
        user_id=u.id,
        reference=f"DEP_{uuid.uuid4().hex[:8]}",
        amount=Decimal("1500.00"),
        status=TransactionStatus.SUCCESS,
        tx_type=TransactionType.WALLET_FUND,
    )
    db.add(tx)
    db.commit()
    assert has_user_funded_wallet(db, u.id) is True


def test_cumulative_2gb_welcome_bonus_flow(db):
    u = create_user(db, "cumul")

    # 1. User deposits ₦2,000
    deposit_tx = Transaction(
        user_id=u.id,
        reference=f"DEP_{uuid.uuid4().hex[:8]}",
        amount=Decimal("2000.00"),
        status=TransactionStatus.SUCCESS,
        tx_type=TransactionType.WALLET_FUND,
    )
    db.add(deposit_tx)
    db.commit()

    # 2. First purchase: 1GB Data
    tx1 = Transaction(
        user_id=u.id,
        reference=f"DATA_{uuid.uuid4().hex[:8]}",
        amount=Decimal("280.00"),
        status=TransactionStatus.SUCCESS,
        tx_type=TransactionType.DATA,
        data_plan_code="MTN-1GB-SME",
        network="MTN",
    )
    db.add(tx1)
    db.commit()

    # Check total GB
    total_gb_1 = get_user_total_data_gb(db, u.id)
    assert total_gb_1 == 1.0

    # Evaluate bonus: shouldn't award yet (1GB < 2GB)
    with patch("app.services.commissions.get_tenant_brand", return_value="kullomadata"):
        bonus1 = check_and_award_welcome_data_bonus(db, user=u)
        assert bonus1 is None

    # 3. Second purchase: Another 1GB Data
    tx2 = Transaction(
        user_id=u.id,
        reference=f"DATA_{uuid.uuid4().hex[:8]}",
        amount=Decimal("280.00"),
        status=TransactionStatus.SUCCESS,
        tx_type=TransactionType.DATA,
        data_plan_code="MTN-1GB-SME",
        network="MTN",
    )
    db.add(tx2)
    db.commit()

    # Total GB is now 2.0 GB (1GB + 1GB)
    total_gb_2 = get_user_total_data_gb(db, u.id)
    assert total_gb_2 == 2.0

    # Evaluate bonus: now meets 2GB threshold -> awards ₦300!
    with patch("app.services.commissions.get_tenant_brand", return_value="kullomadata"):
        bonus2 = check_and_award_welcome_data_bonus(db, user=u)
        assert bonus2 == Decimal("300.00")

    # Check wallet balance: initial 1000 + 300 = 1300
    wallet = db.query(Wallet).filter(Wallet.user_id == u.id).first()
    assert wallet.balance == Decimal("1300.00")

    # Check transaction created
    bonus_tx = db.query(Transaction).filter(Transaction.reference == f"WELCOME_2GB_{u.id}").first()
    assert bonus_tx is not None
    assert bonus_tx.amount == Decimal("300.00")
    assert bonus_tx.status == TransactionStatus.SUCCESS

    # 4. Third purchase: Another 1GB (total 3GB)
    tx3 = Transaction(
        user_id=u.id,
        reference=f"DATA_{uuid.uuid4().hex[:8]}",
        amount=Decimal("280.00"),
        status=TransactionStatus.SUCCESS,
        tx_type=TransactionType.DATA,
        data_plan_code="MTN-1GB-SME",
        network="MTN",
    )
    db.add(tx3)
    db.commit()

    # Idempotency check: should NOT award again!
    with patch("app.services.commissions.get_tenant_brand", return_value="kullomadata"):
        bonus3 = check_and_award_welcome_data_bonus(db, user=u)
        assert bonus3 is None
        # Wallet balance remains 1300
        db.refresh(wallet)
        assert wallet.balance == Decimal("1300.00")


def test_reconcile_missing_welcome_bonuses(db):
    u = create_user(db, "reconcile")

    # User funded and bought 2GB in a single order
    dep = Transaction(
        user_id=u.id,
        reference=f"DEP_{uuid.uuid4().hex[:8]}",
        amount=Decimal("1000.00"),
        status=TransactionStatus.SUCCESS,
        tx_type=TransactionType.WALLET_FUND,
    )
    tx_2gb = Transaction(
        user_id=u.id,
        reference=f"DATA_{uuid.uuid4().hex[:8]}",
        amount=Decimal("560.00"),
        status=TransactionStatus.SUCCESS,
        tx_type=TransactionType.DATA,
        data_plan_code="MTN-2GB-SME",
        network="MTN",
    )
    db.add_all([dep, tx_2gb])
    db.commit()

    with patch("app.services.commissions.get_tenant_brand", return_value="kullomadata"):
        awarded = reconcile_missing_welcome_bonuses(db, limit=10)
        user_bonuses = [a for a in awarded if a["user_id"] == u.id]
        assert len(user_bonuses) == 1
        assert user_bonuses[0]["amount"] == 300.0


def test_kulloma_bonus_works_even_when_request_is_meledata_host(db):
    """
    Ensures that when a Kulloma Data user buys data through the shared backend
    where the Request host header is api.meledata.ng, the bonus is still awarded.
    """
    from starlette.requests import Request
    from unittest.mock import MagicMock

    u = create_user(db, "kulloma_host_test", brand="kullomadata")

    dep = Transaction(
        user_id=u.id,
        reference=f"DEP_{uuid.uuid4().hex[:8]}",
        amount=Decimal("1000.00"),
        status=TransactionStatus.SUCCESS,
        tx_type=TransactionType.WALLET_FUND,
    )
    tx = Transaction(
        user_id=u.id,
        reference=f"DATA_{uuid.uuid4().hex[:8]}",
        amount=Decimal("560.00"),
        status=TransactionStatus.SUCCESS,
        tx_type=TransactionType.DATA,
        data_plan_code="MTN-2GB-SME",
        network="MTN",
    )
    db.add_all([dep, tx])
    db.commit()

    # Mock request with Host header set to api.meledata.ng
    req = MagicMock(spec=Request)
    req.headers = {"host": "api.meledata.ng"}
    req.url = MagicMock()
    req.url.hostname = "api.meledata.ng"

    bonus = check_and_award_welcome_data_bonus(db, user=u, request=req)
    assert bonus == Decimal("300.00")

    # Wallet credited
    w = db.query(Wallet).filter(Wallet.user_id == u.id).first()
    assert w.balance == Decimal("1300.00")


def test_kulloma_bonus_immune_to_global_meledata_disabled_setting(db):
    """
    Ensures that if Mele Data admin saves settings with comm_enable_welcome_bonus = 'false',
    Kulloma Data's bonus remains active and unaffected.
    """
    from app.models.system_setting import SystemSetting
    s = db.query(SystemSetting).filter(SystemSetting.key == "comm_enable_welcome_bonus").first()
    if not s:
        db.add(SystemSetting(key="comm_enable_welcome_bonus", value="false"))
    else:
        s.value = "false"
    db.commit()

    u = create_user(db, "immune_test", brand="kullomadata")

    dep = Transaction(
        user_id=u.id,
        reference=f"DEP_{uuid.uuid4().hex[:8]}",
        amount=Decimal("1000.00"),
        status=TransactionStatus.SUCCESS,
        tx_type=TransactionType.WALLET_FUND,
    )
    tx = Transaction(
        user_id=u.id,
        reference=f"DATA_{uuid.uuid4().hex[:8]}",
        amount=Decimal("560.00"),
        status=TransactionStatus.SUCCESS,
        tx_type=TransactionType.DATA,
        data_plan_code="MTN-2GB-SME",
        network="MTN",
    )
    db.add_all([dep, tx])
    db.commit()

    bonus = check_and_award_welcome_data_bonus(db, user=u)
    assert bonus == Decimal("300.00")


def test_meledata_user_never_awarded_kulloma_bonus(db):
    """
    Strict security check: Users on Mele Data must NEVER receive the Kulloma Data bonus.
    """
    u = create_user(db, "mele_user", brand="meledata")

    dep = Transaction(
        user_id=u.id,
        reference=f"DEP_{uuid.uuid4().hex[:8]}",
        amount=Decimal("1000.00"),
        status=TransactionStatus.SUCCESS,
        tx_type=TransactionType.WALLET_FUND,
    )
    tx = Transaction(
        user_id=u.id,
        reference=f"DATA_{uuid.uuid4().hex[:8]}",
        amount=Decimal("560.00"),
        status=TransactionStatus.SUCCESS,
        tx_type=TransactionType.DATA,
        data_plan_code="MTN-2GB-SME",
        network="MTN",
    )
    db.add_all([dep, tx])
    db.commit()

    bonus = check_and_award_welcome_data_bonus(db, user=u)
    assert bonus is None

    w = db.query(Wallet).filter(Wallet.user_id == u.id).first()
    assert w.balance == Decimal("1000.00")


def test_provider_prefixed_plan_code_parsed(db):
    """
    Tests that plan codes with provider prefixes (e.g. telecom_abode:mtn:605)
    resolve accurately via DataPlan lookup to 2GB.
    """
    from app.models.data_plan import DataPlan

    dp = DataPlan(
        network="MTN",
        data_type="SME",
        plan_name="MTN SME 2.0GB",
        data_size="2GB",
        validity="30 Days",
        plan_code="mtn_sme_2gb",
        provider="telecom_abode",
        provider_plan_id="605",
        base_price=Decimal("500"),
        is_active=True,
    )
    db.add(dp)
    db.commit()

    u = create_user(db, "prefix_test", brand="kullomadata")
    dep = Transaction(
        user_id=u.id,
        reference=f"DEP_{uuid.uuid4().hex[:8]}",
        amount=Decimal("1000.00"),
        status=TransactionStatus.SUCCESS,
        tx_type=TransactionType.WALLET_FUND,
    )
    tx = Transaction(
        user_id=u.id,
        reference=f"DATA_{uuid.uuid4().hex[:8]}",
        amount=Decimal("560.00"),
        status=TransactionStatus.SUCCESS,
        tx_type=TransactionType.DATA,
        data_plan_code="telecom_abode:mtn:605",
        provider_plan_id="605",
        network="MTN",
    )
    db.add_all([dep, tx])
    db.commit()

    total_gb = get_user_total_data_gb(db, u.id)
    assert total_gb == 2.0

    bonus = check_and_award_welcome_data_bonus(db, user=u)
    assert bonus == Decimal("300.00")

