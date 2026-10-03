import pytest
from decimal import Decimal
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app, _ensure_data_plan_dispatch_columns
from app.core.database import SessionLocal, Base, engine
from app.models import User, Wallet, DataPlan, Transaction, TransactionStatus, TransactionType
from app.core.security import hash_password, create_access_token

Base.metadata.create_all(bind=engine)
_ensure_data_plan_dispatch_columns()

@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

@pytest.fixture
def test_user(db):
    email = "multidispatch_user@example.com"
    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(
            email=email,
            hashed_password=hash_password("password"),
            full_name="MultiDispatch User",
            phone_number="08011223344",
            referral_code="REF-MULTI1"
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    wallet = db.query(Wallet).filter(Wallet.user_id == user.id).first()
    if not wallet:
        wallet = Wallet(user_id=user.id, balance=Decimal("10000.00"))
        db.add(wallet)
        db.commit()
    else:
        wallet.balance = Decimal("10000.00")
        db.commit()

    return user

@pytest.fixture
def auth_headers(test_user):
    token = create_access_token(subject=str(test_user.id), role=test_user.role.value)
    return {"Authorization": f"Bearer {token}"}

@pytest.fixture
def mtn_10gb_split_plan(db):
    plan_code = "mtn_10gb_split_test"
    plan = db.query(DataPlan).filter(DataPlan.plan_code == plan_code).first()
    if not plan:
        plan = DataPlan(
            network="mtn",
            plan_code=plan_code,
            plan_name="MTN 10GB (2x5GB)",
            data_size="10GB",
            validity="30 Days",
            base_price=Decimal("2600.00"),
            display_price=Decimal("2700.00"),
            provider="amigo",
            provider_plan_id="amigo_10gb_code",
            dispatch_count=2,
            dispatch_plan_id="amigo_5gb_sub_code",
            is_active=True
        )
        db.add(plan)
        db.commit()
        db.refresh(plan)
    return plan

def test_multi_dispatch_full_success(test_user, auth_headers, mtn_10gb_split_plan, db):
    client = TestClient(app)
    
    with patch("time.sleep", return_value=None):
        with patch("app.services.amigo.AmigoClient.purchase_data") as mock_purchase:
            mock_purchase.side_effect = [
                {"success": True, "status": "delivered", "reference": "ext_ref_part1"},
                {"success": True, "status": "delivered", "reference": "ext_ref_part2"},
            ]
            
            res = client.post(
                "/api/v1/data/purchase",
                headers=auth_headers,
                json={
                    "plan_code": mtn_10gb_split_plan.plan_code,
                    "phone_number": "08012345678",
                    "network": "mtn"
                }
            )
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "success"
            
            # Verify 2 calls to provider with sub-references
            assert mock_purchase.call_count == 2
            call1_args, call1_kwargs = mock_purchase.call_args_list[0]
            call2_args, call2_kwargs = mock_purchase.call_args_list[1]
            assert "_p1" in call1_kwargs["idempotency_key"]
            assert "_p2" in call2_kwargs["idempotency_key"]
            assert call1_args[0]["plan"] == "amigo_5gb_sub_code"
            assert call2_args[0]["plan"] == "amigo_5gb_sub_code"

            # Verify wallet debited by 2700, no refund
            wallet = db.query(Wallet).filter(Wallet.user_id == test_user.id).first()
            assert wallet.balance == Decimal("7300.00")

def test_multi_dispatch_full_failure_refunds_all(test_user, auth_headers, mtn_10gb_split_plan, db):
    client = TestClient(app)
    
    with patch("time.sleep", return_value=None):
        with patch("app.services.amigo.AmigoClient.purchase_data") as mock_purchase:
            mock_purchase.side_effect = [
                {"success": False, "status": "failed", "message": "Network out of stock"},
                {"success": False, "status": "failed", "message": "Network out of stock"},
            ]
            
            res = client.post(
                "/api/v1/data/purchase",
                headers=auth_headers,
                json={
                    "plan_code": mtn_10gb_split_plan.plan_code,
                    "phone_number": "08012345678",
                    "network": "mtn"
                }
            )
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "failed"
            
            # Wallet should be fully refunded back to 10,000
            wallet = db.query(Wallet).filter(Wallet.user_id == test_user.id).first()
            assert wallet.balance == Decimal("10000.00")

def test_multi_dispatch_partial_success_refunds_half(test_user, auth_headers, mtn_10gb_split_plan, db):
    client = TestClient(app)
    
    with patch("time.sleep", return_value=None):
        with patch("app.services.amigo.AmigoClient.purchase_data") as mock_purchase:
            # 1st succeeds, 2nd fails
            mock_purchase.side_effect = [
                {"success": True, "status": "delivered", "reference": "ext_ref_p1"},
                {"success": False, "status": "failed", "message": "Provider timeout"},
            ]
            
            res = client.post(
                "/api/v1/data/purchase",
                headers=auth_headers,
                json={
                    "plan_code": mtn_10gb_split_plan.plan_code,
                    "phone_number": "08012345678",
                    "network": "mtn"
                }
            )
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "success"
            
            # Cost was 2,700. Half is 1,350.
            # Balance started at 10,000 -> debited 2,700 (7,300) -> refunded 1,350 (8,650).
            wallet = db.query(Wallet).filter(Wallet.user_id == test_user.id).first()
            assert wallet.balance == Decimal("8650.00")
            
            tx = db.query(Transaction).filter(Transaction.reference == data["reference"]).first()
            assert tx.status == TransactionStatus.SUCCESS
            assert "Partial delivery: 1/2 delivered" in tx.failure_reason
            assert "1,350.00 refunded" in tx.failure_reason
