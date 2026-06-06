import os
os.environ["DATABASE_URL"] = "sqlite:///./test.db"

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from app.core.database import SessionLocal, Base, engine
from app.main import app
from app.models.user import User, UserRole
from app.models.wallet import Wallet
from app.core.security import hash_password

Base.metadata.create_all(bind=engine)

def _seed_kyc_user(db, *, email: str, full_name: str, referral_code: str):
    user = User(
        email=email,
        full_name=full_name,
        hashed_password=hash_password("password"),
        role=UserRole.USER,
        referral_code=referral_code,
    )
    db.add(user)
    db.flush()
    wallet = Wallet(user_id=user.id, balance=0.0)
    db.add(wallet)
    db.flush()
    return user

def test_kyc_bvn_nin_uniqueness():
    db = SessionLocal()
    try:
        # Create users
        u1 = _seed_kyc_user(db, email="kyc1@example.com", full_name="KYC User One", referral_code="ref111")
        u2 = _seed_kyc_user(db, email="kyc2@example.com", full_name="KYC User Two", referral_code="ref222")
        db.commit()
        
        with TestClient(app) as client:
            # Login as User 1
            login_resp = client.post(
                "/api/v1/auth/login",
                json={"email": u1.email, "password": "password"}
            )
            assert login_resp.status_code == 200
            token1 = login_resp.json()["access_token"]
            headers1 = {"Authorization": f"Bearer {token1}"}
            
            # Login as User 2
            login_resp2 = client.post(
                "/api/v1/auth/login",
                json={"email": u2.email, "password": "password"}
            )
            assert login_resp2.status_code == 200
            token2 = login_resp2.json()["access_token"]
            headers2 = {"Authorization": f"Bearer {token2}"}
            
            dummy_bvn = "12345678901"
            
            # Mock monnify account reservation
            with patch("app.api.v1.endpoints.wallet.reserve_monnify_account") as mock_reserve:
                mock_reserve.return_value = {
                    "responseCode": "0",
                    "responseMessage": "success",
                    "responseBody": {
                        "reservationReference": "dummy_ref_123",
                        "accounts": [
                            {
                                "bankCode": "035",
                                "bankName": "Wema Bank",
                                "accountNumber": "9998887771",
                                "accountName": "MMTECHGLOBE/KYC User One"
                            }
                        ]
                    }
                }
                
                # User 1 registers with dummy BVN
                resp = client.post(
                    "/api/v1/wallet/bank-transfer-accounts",
                    json={"bvn": dummy_bvn, "nin": ""},
                    headers=headers1
                )
                assert resp.status_code == 200
                
                # Verify user1 has bvn_hash populated
                db.refresh(u1)
                assert u1.bvn_hash is not None
                
                # User 2 tries to register with the same dummy BVN
                resp_dup = client.post(
                    "/api/v1/wallet/bank-transfer-accounts",
                    json={"bvn": dummy_bvn, "nin": ""},
                    headers=headers2
                )
                assert resp_dup.status_code == 400
                assert "bvn is already linked" in resp_dup.json()["detail"].lower()
    finally:
        db.close()
