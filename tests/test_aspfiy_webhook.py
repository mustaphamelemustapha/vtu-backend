import hashlib
import json
import secrets
import pytest
from decimal import Decimal
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import SessionLocal, Base, engine
from app.models import User, VirtualAccount, VirtualAccountProvider, VirtualAccountStatus, Wallet, Transaction
from app.models.integration import PaymentGateway
from app.core.config import get_settings

Base.metadata.create_all(bind=engine)
settings = get_settings()

@pytest.fixture
def client():
    return TestClient(app)

def test_aspfiy_webhook_health(client):
    for path in ("/api/v1/webhooks/aspfiy", "/api/v1/webhooks/aspfiy/", "/api/v1/webhooks/aspify", "/api/v1/webhooks/asfiy"):
        response = client.get(path)
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
                customer_reference=f"OTHER_APP_{user.id}_aspfiy_paga",
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

def test_aspfiy_webhook_comma_amount_and_alternate_event(client):
    db = SessionLocal()
    try:
        email = "test_aspfiy_comma@example.com"
        user = db.query(User).filter(User.email == email).first()
        if not user:
            user = User(
                email=email,
                hashed_password="pw",
                full_name="Comma User",
                phone_number="08011223344",
                referral_code=f"REF-{secrets.token_hex(4)}"
            )
            db.add(user)
            db.commit()
            db.refresh(user)

        wallet = db.query(Wallet).filter(Wallet.user_id == user.id).first()
        init_balance = wallet.balance if wallet else Decimal("0")

        # Payload with formatted amount "1,500.00", alternate event "SUCCESS", and alternate account key "account_no"
        payload = {
            "event": "SUCCESS",
            "data": {
                "reference": f"asp_comma_{secrets.token_hex(4)}",
                "amount": "1,500.00",
                "account_no": "2300112233",
                "customer": {"email": email}
            }
        }

        # Uses alias endpoint /api/v1/webhooks/aspify
        response = client.post(
            "/api/v1/webhooks/aspify",
            json=payload,
            headers={"x-aspfiy-signature": "test_secret_key_123"}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "success"

        wallet = db.query(Wallet).filter(Wallet.user_id == user.id).first()
        assert wallet is not None
        assert wallet.balance >= Decimal("1500.00")
    finally:
        db.close()

def test_aspfiy_webhook_db_secret_key(client):
    db = SessionLocal()
    try:
        email = "test_aspfiy_dbsec@example.com"
        user = db.query(User).filter(User.email == email).first()
        if not user:
            user = User(
                email=email,
                hashed_password="pw",
                full_name="DBSec User",
                phone_number="08099887766",
                referral_code=f"REF-{secrets.token_hex(4)}"
            )
            db.add(user)
            db.commit()
            db.refresh(user)

        # Clear settings secret key and configure in DB PaymentGateway
        settings.aspfiy_secret_key = ""
        gw = db.query(PaymentGateway).filter(PaymentGateway.identifier == "aspfiy").first()
        if not gw:
            gw = PaymentGateway(name="Aspfiy", identifier="aspfiy", secret_key="db_secret_key_999", is_active=True)
            db.add(gw)
        else:
            gw.secret_key = "db_secret_key_999"
            gw.is_active = True
        db.commit()

        sig = hashlib.sha256(b"db_secret_key_999").hexdigest()

        payload = {
            "type": "PAYMENT_NOTIFICATION",
            "data": {
                "transaction_ref": f"asp_dbsec_{secrets.token_hex(4)}",
                "amount": "500",
                "virtual_account_number": "2300554433",
                "customer": {"email": email}
            }
        }

        response = client.post(
            "/api/v1/webhooks/aspfiy/",
            json=payload,
            headers={"signature": sig}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "success"
    finally:
        db.close()


def test_aspfiy_webhook_render_forward(client, monkeypatch):
    import respx
    import httpx

    with respx.mock(base_url='https://api.meledata.ng') as respx_mock:
        route = respx_mock.post('/api/v1/webhooks/aspfiy').respond(200, json={'status': 'success', 'message': 'forwarded'})

        payload = {'event': 'payment.success', 'data': {'reference': 'fwd_ref_1', 'amount': '500'}}
        response = client.post(
            '/api/v1/webhooks/aspfiy',
            json=payload,
            headers={'host': 'vtu-backend-8gsi.onrender.com', 'x-wiaxy-signature': 'sig123'}
        )
        assert response.status_code == 200
        assert response.json()['message'] == 'forwarded'
        assert route.called


def test_aspfiy_webhook_meledata_tenant_rejected(client):
    """
    Ensure webhooks targeting MELE DATA via header or host are cleanly decommissioned/rejected.
    """
    payload = {'event': 'payment.success', 'data': {'reference': 'tx_mele_1', 'amount': '500'}}
    
    # 1. Scoped via X-App-Brand
    resp1 = client.post(
        '/api/v1/webhooks/aspfiy',
        json=payload,
        headers={'x-app-brand': 'meledata'}
    )
    assert resp1.status_code == 200
    assert resp1.json()['status'] == 'decommissioned'

    # 2. Scoped via Host header
    resp2 = client.post(
        '/api/v1/webhooks/aspfiy',
        json=payload,
        headers={'host': 'api.meledata.ng'}
    )
    assert resp2.status_code == 200
    assert resp2.json()['status'] == 'decommissioned'


def test_aspfiy_webhook_meledata_reference_dropped(client):
    """
    Ensure webhooks with MELEDATA or AXISVTU customer references are dropped even on shared endpoints.
    """
    payload = {
        'event': 'payment.success',
        'data': {
            'reference': 'MELEDATA_99_aspfiy_paga',
            'merchant_reference': 'MELEDATA_99_aspfiy_paga',
            'amount': '500'
        }
    }
    resp = client.post(
        '/api/v1/webhooks/aspfiy',
        json=payload,
        headers={'host': 'api.mzdata.org'}
    )
    assert resp.status_code == 200
    assert resp.json()['status'] == 'decommissioned'


def test_aspfiy_other_tenant_webhook_remains_active(client):
    """
    Non-regression: Ensure other tenants (like MZ DATA) can still process valid Aspfiy webhooks.
    """
    db = SessionLocal()
    try:
        email = "other_tenant_user@example.com"
        user = db.query(User).filter(User.email == email).first()
        if not user:
            user = User(
                email=email,
                hashed_password="pw",
                full_name="MZ User",
                phone_number="08099887766",
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
                "reference": f"mz_tx_{secrets.token_hex(4)}",
                "amount": "300.00",
                "customer": {"email": email}
            }
        }

        resp = client.post(
            "/api/v1/webhooks/aspfiy",
            json=payload,
            headers={
                "x-app-brand": "mzdata",
                "x-wiaxy-signature": sig
            }
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"
    finally:
        db.close()


def test_aspfiy_webhook_existing_meledata_db_account_dropped(client):
    """
    Ensure webhooks matching an existing MELE DATA account in the database are dropped.
    """
    db = SessionLocal()
    try:
        email = "mele_persisted_user@example.com"
        user = db.query(User).filter(User.email == email).first()
        if not user:
            user = User(
                email=email,
                hashed_password="pw",
                full_name="Mele DB User",
                phone_number="08055667788",
                referral_code=f"REF-{secrets.token_hex(4)}"
            )
            db.add(user)
            db.commit()
            db.refresh(user)

        acc_num = "2300888111"
        va = db.query(VirtualAccount).filter(VirtualAccount.account_number == acc_num).first()
        if not va:
            va = VirtualAccount(
                user_id=user.id,
                provider=VirtualAccountProvider.ASPFIY,
                account_number=acc_num,
                account_name="Mele DB User",
                bank_name="Paga",
                bank_code="000",
                customer_reference=f"MELEDATA_{user.id}_aspfiy_paga",
                reservation_reference=f"MELEDATA_{user.id}_aspfiy_paga",
                status=VirtualAccountStatus.ACTIVE
            )
            db.add(va)
            db.commit()

        settings.aspfiy_secret_key = "test_secret_key_123"
        sig = hashlib.md5(b"test_secret_key_123").hexdigest()

        payload = {
            "event": "payment.success",
            "data": {
                "reference": f"tx_unknown_ref_{secrets.token_hex(4)}",
                "amount": "400.00",
                "paid_into": acc_num,
                "customer": {"email": email}
            }
        }

        resp = client.post(
            "/api/v1/webhooks/aspfiy",
            json=payload,
            headers={"x-wiaxy-signature": sig}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "decommissioned"
    finally:
        db.close()


