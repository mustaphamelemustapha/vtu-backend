import hashlib
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.core.database import get_db
from app.models import User, UserRole, Wallet, DataPlan, Transaction, WalletLedger
from app.models.service_transaction import ServiceTransaction
from app.core.security import create_access_token


def test_developer_workflow(db: Session = next(get_db())):
    client = TestClient(app)

    # 1. Create a test user
    email = "dev_workflow_test@meledata.ng"
    # Clean up existing test user
    db.query(User).filter(User.email == email).delete()
    db.commit()

    test_user = User(
        email=email,
        full_name="Dev Tester",
        hashed_password="mockpassword",
        role=UserRole.USER,
        referral_code="DEVTESTREF",
        is_active=True,
    )
    db.add(test_user)
    db.commit()
    db.refresh(test_user)

    from app.services.wallet import get_or_create_wallet
    wallet = get_or_create_wallet(db, test_user.id)
    wallet.balance = 5000.0
    db.commit()

    token = create_access_token(str(test_user.id), test_user.role.value)
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Get status (should be "none")
    res = client.get("/api/v1/developer/status", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["is_developer"] is False
    assert data["developer_status"] == "none"
    assert data["has_keys"] is False

    # 3. Apply to be developer (auto-approved with keys)
    res = client.post("/api/v1/developer/apply", json={"additional_info": "We need API access"}, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["developer_status"] == "approved"
    assert data["is_developer"] is True
    assert "api_public_key" in data

    # 6. Generate keys after approval (should succeed)
    res = client.post("/api/v1/developer/keys/generate", headers=headers)
    assert res.status_code == 200
    keys = res.json()
    assert "api_public_key" in keys
    assert "api_secret_key" in keys
    assert keys["api_public_key"].lower().startswith("mele_pub_")
    assert keys["api_secret_key"].lower().startswith("mele_live_")

    # Verify secret key hash is stored correctly in DB
    db.refresh(test_user)
    expected_hash = hashlib.sha256(keys["api_secret_key"].encode("utf-8")).hexdigest()
    assert test_user.api_secret_key_hash == expected_hash

    # 7. Test Developer Bearer Auth on API endpoints
    dev_headers = {"Authorization": f"Bearer {keys['api_secret_key']}"}
    res = client.get("/api/v1/developer/wallet/balance", headers=dev_headers)
    assert res.status_code == 200
    bal_data = res.json()
    assert bal_data["data"]["balance"] == 5000.0

    # 8. Test invalid Bearer token
    invalid_headers = {"Authorization": "Bearer MELE_SEC_INVALID_TOKEN_HASH_1234"}
    res = client.get("/api/v1/developer/wallet/balance", headers=invalid_headers)
    assert res.status_code == 401

    # 9. Create a test DataPlan
    plan_code = "DEV_TEST_PLAN_1GB"
    # Clean up existing test plans
    db.query(DataPlan).filter(DataPlan.plan_code == plan_code).delete()
    db.commit()

    test_plan = DataPlan(
        network="mtn",
        plan_code=plan_code,
        plan_name="Developer test 1GB",
        data_size="1GB",
        validity="30 days",
        base_price=220.0,
        is_active=True,
    )
    db.add(test_plan)
    db.commit()
    db.refresh(test_plan)

    # 10. Test data purchase API
    purchase_payload = {
        "phone_number": "08012345678",
        "network": "MTN",
        "plan_code": plan_code,
        "reference": "TEST_TX_REF_001"
    }
    # Mocking provider call might make it pending/failed/success depending on sandbox settings.
    # In test mode, Amigo test mode is true, so it will succeed locally!
    res = client.post("/api/v1/developer/data/purchase", json=purchase_payload, headers=dev_headers)
    assert res.status_code == 200
    purchase_res = res.json()
    assert purchase_res["status"] in {"success", "pending", "failed", "refunded"}
    
    # Assert transaction was recorded in DB
    tx_rec = db.query(Transaction).filter(Transaction.user_id == test_user.id, Transaction.reference == f"DEV_{test_user.id}_{purchase_payload['reference']}").first()
    assert tx_rec is not None

    # Test Sandbox Test Mode Purchase
    test_sec_key = keys["api_secret_key"].replace("mele_live_", "mele_test_")
    test_dev_headers = {"Authorization": f"Bearer {test_sec_key}"}
    test_res = client.post("/api/v1/developer/data/purchase", json={
        "phone_number": "08012345678",
        "network": "MTN",
        "plan_id": test_plan.id,
        "reference": "SANDBOX_DATA_001"
    }, headers=test_dev_headers)
    assert test_res.status_code == 200
    assert test_res.json()["status"] is True

    # 11. Test airtime purchase API
    airtime_payload = {
        "phone_number": "08012345678",
        "network": "mtn",
        "amount": 100.0,
        "reference": "TEST_TX_REF_002"
    }
    res = client.post("/api/v1/developer/airtime/purchase", json=airtime_payload, headers=dev_headers)
    assert res.status_code == 200
    airtime_res = res.json()
    assert airtime_res["status"] in {"success", "pending", "failed", "refunded"}

    # Assert airtime transaction was recorded in DB
    airtime_tx_rec = db.query(ServiceTransaction).filter(ServiceTransaction.user_id == test_user.id, ServiceTransaction.reference == f"DEV_{test_user.id}_{airtime_payload['reference']}").first()
    assert airtime_tx_rec is not None

    # 12. Clean up test records
    db.query(Transaction).filter(Transaction.user_id == test_user.id).delete()
    db.query(ServiceTransaction).filter(ServiceTransaction.user_id == test_user.id).delete()
    db.query(WalletLedger).filter(WalletLedger.wallet_id == wallet.id).delete()
    db.delete(test_plan)
    db.delete(wallet)
    db.delete(test_user)
    db.commit()
