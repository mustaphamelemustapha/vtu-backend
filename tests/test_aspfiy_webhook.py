import hashlib
import json
import secrets
import pytest
from decimal import Decimal
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import SessionLocal, Base, engine
from app.models import User, VirtualAccount, VirtualAccountProvider, VirtualAccountStatus, Wallet, Transaction
from app.core.config import get_settings

Base.metadata.create_all(bind=engine)
settings = get_settings()

@pytest.fixture
def client():
    return TestClient(app)

def test_aspfiy_webhook_health(client):
    response = client.get("/api/v1/webhooks/aspfiy")
    assert response.status_code == 200
    assert response.json() == {"status": "active", "provider": "aspfiy"}

def test_aspfiy_webhook_with_valid_signature(client):
    db = SessionLocal()
    try:
        email = "test_aspfiy_sig@example.com"
        user = db.query(User).filter(User.email == email).first()
        if not user:
            user = User(
                email=email,
                hashed_password="pw",
                full_name="Mustapha Test",
                phone_number="08012345678",
                referral_code=f"REF-{secrets.token_hex(4)}"
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        
        settings.aspfiy_secret_key = "test_secret_key_123"
        sig = hashlib.md5(b"test_secret_key_123").hexdigest()

        payload = {
            "event": "payment.success",
            "data": {
                "reference": "asp_tx_valid_sig_001",
                "amount": "250.00",
                "paid_into": "2300925737",
                "customer": {"email": email}
            }
        }
        
        response = client.post(
            "/api/v1/webhooks/aspfiy",
            json=payload,
            headers={"x-wiaxy-signature": sig}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "success"

        # Verify wallet
        wallet = db.query(Wallet).filter(Wallet.user_id == user.id).first()
        assert wallet is not None
        assert wallet.balance >= Decimal("250.00")
    finally:
        db.close()

def test_aspfiy_webhook_fallback_when_account_matched(client):
    db = SessionLocal()
    try:
        email = "test_aspfiy_fallback@example.com"
        user = db.query(User).filter(User.email == email).first()
        if not user:
            user = User(
                email=email,
                hashed_password="pw",
                full_name="Fallback User",
                phone_number="08087654321",
                referral_code=f"REF-{secrets.token_hex(4)}"
            )
            db.add(user)
            db.commit()
            db.refresh(user)

        va = db.query(VirtualAccount).filter(VirtualAccount.account_number == "2300999999").first()
        if not va:
            va = VirtualAccount(
                user_id=user.id,
                provider=VirtualAccountProvider.ASPFIY,
                account_number="2300999999",
                account_name="Fallback User",
                bank_name="Paga",
                bank_code="000",
                customer_reference=f"AXISVTU_{user.id}_aspfiy_paga",
                reservation_reference="res_ref_123",
                status=VirtualAccountStatus.ACTIVE
            )
            db.add(va)
            db.commit()

        payload = {
            "event": "payment.success",
            "data": {
                "reference": "asp_tx_fallback_002",
                "amount": "100.00",
                "paid_into": "2300999999",
                "customer": {"email": email}
            }
        }

        # Post with completely wrong signature
        response = client.post(
            "/api/v1/webhooks/aspfiy",
            json=payload,
            headers={"x-wiaxy-signature": "completely_wrong_signature"}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "success"

        wallet = db.query(Wallet).filter(Wallet.user_id == user.id).first()
        assert wallet.balance >= Decimal("100.00")
    finally:
        db.close()

def test_aspfiy_webhook_unknown_user_wrong_signature_fails(client):
    payload = {
        "event": "payment.success",
        "data": {
            "reference": "asp_tx_fake_999",
            "amount": "50000.00",
            "paid_into": "9999999999",
            "customer": {"email": "completely_unknown_attacker@example.com"}
        }
    }
    response = client.post(
        "/api/v1/webhooks/aspfiy",
        json=payload,
        headers={"x-wiaxy-signature": "bogus_signature"}
    )
    assert response.status_code == 401
