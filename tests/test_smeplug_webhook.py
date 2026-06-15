import os
os.environ["DATABASE_URL"] = "sqlite:///./test.db"

import pytest
from fastapi.testclient import TestClient
from decimal import Decimal
from unittest.mock import patch, MagicMock

from app.core.database import SessionLocal, Base, engine
from app.main import app
from app.models import User, UserRole, Wallet, Transaction, TransactionStatus, TransactionType
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

        # Seed a pending transaction
        tx_ref = "TX-FAIL-123"
        tx = Transaction(
            user_id=user.id,
            amount=Decimal("200.00"),
            tx_type=TransactionType.DATA,
            status=TransactionStatus.PENDING,
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
