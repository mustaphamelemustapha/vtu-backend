import os
os.environ["DATABASE_URL"] = "sqlite:///./test.db"

import pytest
from fastapi.testclient import TestClient
from decimal import Decimal
from unittest.mock import patch, MagicMock

from datetime import datetime, timedelta, timezone
from app.core.database import SessionLocal, Base, engine
from app.main import app
from app.models import User, UserRole, Wallet, Transaction, TransactionStatus, TransactionType, DataPlan
from app.core.security import hash_password
from app.core.config import get_settings

Base.metadata.create_all(bind=engine)
settings = get_settings()

def _seed_test_user(db, email: str, role: UserRole = UserRole.USER) -> User:
    # Clean up existing user if any
    db.query(Wallet).filter(Wallet.user_id.in_(db.query(User.id).filter(User.email == email))).delete(synchronize_session=False)
    db.query(Transaction).filter(Transaction.user_id.in_(db.query(User.id).filter(User.email == email))).delete(synchronize_session=False)
    db.query(User).filter(User.email == email).delete(synchronize_session=False)
    db.commit()

    user = User(
        email=email,
        full_name="Webhook Tester",
        hashed_password=hash_password("password"),
        role=role,
        is_verified=True,
        referral_code=f"REF-{email.split('@')[0]}",
    )
    db.add(user)
    db.flush()
    wallet = Wallet(user_id=user.id, balance=Decimal("1000.00"))
    db.add(wallet)
    db.flush()
    db.commit()
    return user

def test_smeplug_webhook_unauthorized():
    db = SessionLocal()
    try:
        # Configure a secret
        original_secret = settings.smeplug_webhook_secret
        settings.smeplug_webhook_secret = "test_webhook_secret_key"

        with TestClient(app) as client:
            # Send without header
            response = client.post(
                "/api/v1/webhooks/smeplug",
                json={"transaction": {"status": "success", "customer_reference": "TX-123"}}
            )
            assert response.status_code == 401

            # Send with invalid header
            response = client.post(
                "/api/v1/webhooks/smeplug",
                json={"transaction": {"status": "success", "customer_reference": "TX-123"}},
                headers={"Authorization": "Bearer wrong_secret"}
            )
            assert response.status_code == 401

        settings.smeplug_webhook_secret = original_secret
    finally:
        db.close()

def test_smeplug_webhook_success():
    db = SessionLocal()
    try:
        original_secret = settings.smeplug_webhook_secret
        settings.smeplug_webhook_secret = "test_webhook_secret_key"

        user = _seed_test_user(db, "webhook_success@example.com")
        
        # Seed a pending transaction
        tx_ref = "TX-SUCCESS-123"
        tx = Transaction(
            user_id=user.id,
            amount=Decimal("500.00"),
            tx_type=TransactionType.DATA,
            status=TransactionStatus.PENDING,
            reference=tx_ref,
            network="airtel",
            recipient_phone="09012345678",
        )
        db.add(tx)
        db.commit()

        # Mock trigger_referral_data_activity to avoid dependency logic issues
        with patch("app.services.referrals.trigger_referral_data_activity") as mock_trigger:
            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/webhooks/smeplug",
                    json={
                        "transaction": {
                            "status": "success",
                            "reference": "provider-ref-999",
                            "customer_reference": tx_ref,
                            "beneficiary": "09012345678",
                            "price": "500.00"
                        }
                    },
                    headers={"Authorization": "Bearer test_webhook_secret_key"}
                )
                assert response.status_code == 200
                assert response.json() == {"status": "ok"}

            # Verify transaction updated to SUCCESS
            db.refresh(tx)
            assert tx.status == TransactionStatus.SUCCESS
            assert tx.external_reference == "provider-ref-999"
            mock_trigger.assert_called_once()

        settings.smeplug_webhook_secret = original_secret
    finally:
        db.close()

def test_smeplug_webhook_failed_refunds():
    db = SessionLocal()
    try:
        original_secret = settings.smeplug_webhook_secret
        settings.smeplug_webhook_secret = ""  # Disables secret check for convenience

        user = _seed_test_user(db, "webhook_fail@example.com")
        wallet = db.query(Wallet).filter(Wallet.user_id == user.id).first()
        initial_balance = wallet.balance

        # Seed an older pending transaction (simulating async failure after in-flight request finished)
        tx_ref = "TX-FAIL-123"
        tx = Transaction(
            user_id=user.id,
            amount=Decimal("200.00"),
            tx_type=TransactionType.DATA,
            status=TransactionStatus.PENDING,
            reference=tx_ref,
            network="airtel",
            recipient_phone="09012345678",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=60),
        )
        db.add(tx)
        db.commit()

        with TestClient(app) as client:
            response = client.post(
                "/api/v1/webhooks/smeplug",
                json={
                    "transaction": {
                        "status": "failed",
                        "reference": "provider-ref-fail",
                        "customer_reference": tx_ref,
                        "beneficiary": "09012345678",
                        "price": "200.00"
                    }
                }
            )
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}

        # Verify transaction updated to REFUNDED
        db.refresh(tx)
        assert tx.status == TransactionStatus.REFUNDED
        assert tx.external_reference == "provider-ref-fail"

        # Verify wallet was refunded
        db.refresh(wallet)
        assert wallet.balance == initial_balance + Decimal("200.00")

        settings.smeplug_webhook_secret = original_secret
    finally:
        db.close()

def test_smeplug_webhook_failed_with_fallback_skips_refund():
    """Verify that when a data plan has a fallback provider configured, SMEPlug failure webhook does NOT refund the wallet."""
    db = SessionLocal()
    try:
        original_secret = settings.smeplug_webhook_secret
        settings.smeplug_webhook_secret = ""

        user = _seed_test_user(db, "webhook_fallback@example.com")
        wallet = db.query(Wallet).filter(Wallet.user_id == user.id).first()
        initial_balance = wallet.balance

        # Ensure a data plan with fallback_provider='amigo' exists
        plan_code = "AIRTEL-TEST-FALLBACK"
        db.query(DataPlan).filter(DataPlan.plan_code == plan_code).delete()
        db.commit()

        plan = DataPlan(
            plan_code=plan_code,
            plan_name="Airtel 1GB Test",
            network="airtel",
            data_size="1GB",
            validity="30 days",
            base_price=Decimal("439.00"),
            provider="smeplug",
            provider_plan_id="101",
            fallback_provider="amigo",
            fallback_provider_plan_id="202",
            is_active=True,
        )
        db.add(plan)
        db.commit()

        # Seed a pending transaction linked to this fallback plan
        tx_ref = "TX-FALLBACK-123"
        tx = Transaction(
            user_id=user.id,
            amount=Decimal("439.00"),
            tx_type=TransactionType.DATA,
            status=TransactionStatus.PENDING,
            reference=tx_ref,
            network="airtel",
            recipient_phone="09012345678",
            data_plan_code=plan_code,
            provider="smeplug",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=60),
        )
        db.add(tx)
        db.commit()

        with TestClient(app) as client:
            response = client.post(
                "/api/v1/webhooks/smeplug",
                json={
                    "transaction": {
                        "status": "failed",
                        "reference": "provider-ref-sme-fail",
                        "customer_reference": tx_ref,
                        "beneficiary": "09012345678",
                        "price": "439.00"
                    }
                }
            )
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}

        # CRITICAL ASSERTION: The transaction must NOT be refunded because fallback is handling it!
        db.refresh(tx)
        assert tx.status == TransactionStatus.PENDING

        # CRITICAL ASSERTION: Wallet balance must NOT be credited!
        db.refresh(wallet)
        assert wallet.balance == initial_balance

        settings.smeplug_webhook_secret = original_secret
    finally:
        db.close()

def test_smeplug_webhook_idempotency():
    db = SessionLocal()
    try:
        original_secret = settings.smeplug_webhook_secret
        settings.smeplug_webhook_secret = ""

        user = _seed_test_user(db, "webhook_idem@example.com")
        wallet = db.query(Wallet).filter(Wallet.user_id == user.id).first()
        initial_balance = wallet.balance

        # Seed a transaction that is already SUCCESS
        tx_ref = "TX-IDEM-123"
        tx = Transaction(
            user_id=user.id,
            amount=Decimal("300.00"),
            tx_type=TransactionType.DATA,
            status=TransactionStatus.SUCCESS,
            reference=tx_ref,
            network="airtel",
            recipient_phone="09012345678",
        )
        db.add(tx)
        db.commit()

        with TestClient(app) as client:
            response = client.post(
                "/api/v1/webhooks/smeplug",
                json={
                    "transaction": {
                        "status": "success",
                        "reference": "provider-ref-idem",
                        "customer_reference": tx_ref,
                        "beneficiary": "09012345678",
                        "price": "300.00"
                    }
                }
            )
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}

        # Balance should remain unchanged
        db.refresh(wallet)
        assert wallet.balance == initial_balance

        settings.smeplug_webhook_secret = original_secret
    finally:
        db.close()

def test_data_purchase_fallback_with_premature_refund_reversed():
    """Simulates the Esther exploit: Primary SMEPlug fails, premature webhook refunds wallet, fallback Amigo delivers.
    The system must detect the refund and re-debit the wallet, leaving the user with remaining balance (not refunded).
    """
    from app.core.security import create_access_token
    from app.services.wallet import credit_wallet
    db = SessionLocal()
    try:
        user = _seed_test_user(db, "esther_exploit_test@example.com")
        wallet = db.query(Wallet).filter(Wallet.user_id == user.id).first()
        # Initial funding: 450 NGN
        wallet.balance = Decimal("450.00")
        db.commit()

        plan_code = "MTN-TEST-ESTHER"
        db.query(DataPlan).filter(DataPlan.plan_code == plan_code).delete()
        db.commit()

        plan = DataPlan(
            plan_code=plan_code,
            plan_name="MTN 1GB Fallback Test",
            network="mtn",
            data_size="1GB",
            validity="30 days",
            base_price=Decimal("439.00"),
            display_price=Decimal("439.00"),
            provider="smeplug",
            provider_plan_id="1001",
            fallback_provider="amigo",
            fallback_provider_plan_id="2002",
            is_active=True,
        )
        db.add(plan)
        db.commit()

        token = create_access_token(subject=str(user.id), role=user.role.value)
        headers = {"Authorization": f"Bearer {token}"}

        # Mock SMEPlug failing, and during Amigo fallback, simulate a premature refund
        def mock_smeplug_purchase(*args, **kwargs):
            return {"status": "failed", "error": "Insufficient SMEPlug balance"}

        def mock_amigo_purchase(*args, **kwargs):
            # Simulate a concurrent webhook that fired and refunded the wallet while Amigo was running
            _sim_db = SessionLocal()
            try:
                _sim_tx = _sim_db.query(Transaction).filter(Transaction.user_id == user.id).order_by(Transaction.id.desc()).first()
                if _sim_tx:
                    _sim_w = _sim_db.query(Wallet).filter(Wallet.user_id == user.id).first()
                    credit_wallet(_sim_db, _sim_w, Decimal("439.00"), f"premature_{_sim_tx.reference}", "Premature SMEPlug failure refund")
                    _sim_tx.status = TransactionStatus.REFUNDED
                    _sim_db.commit()
            finally:
                _sim_db.close()

            return {"success": True, "status": "delivered", "reference": "AMIGO-REF-SUCCESS-123"}

        with patch("app.providers.smeplug_provider.SMEPlugProvider.purchase_network_data", side_effect=mock_smeplug_purchase):
            with patch("app.services.amigo.AmigoClient.purchase_data", side_effect=mock_amigo_purchase):
                with TestClient(app) as client:
                    res = client.post(
                        "/api/v1/data/purchase",
                        headers=headers,
                        json={
                            "plan_code": plan_code,
                            "phone_number": "08012345678",
                            "network": "mtn"
                        }
                    )
                    assert res.status_code == 200
                    data = res.json()
                    assert data["status"] == "success"

        # Verification:
        db.expire_all()
        refreshed_wallet = db.query(Wallet).filter(Wallet.user_id == user.id).first()
        # Balance must be 450 - 439 = 11.00 (the premature refund was reversed!)
        assert refreshed_wallet.balance == Decimal("11.00")

        # Transaction status must be SUCCESS
        tx = db.query(Transaction).filter(Transaction.user_id == user.id).order_by(Transaction.id.desc()).first()
        assert tx.status == TransactionStatus.SUCCESS
        assert tx.provider == "amigo"
        assert tx.external_reference == "AMIGO-REF-SUCCESS-123"

    finally:
        db.close()
