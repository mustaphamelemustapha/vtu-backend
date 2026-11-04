import pytest
import secrets
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import SessionLocal, Base, engine
from app.models import User, VirtualAccount, VirtualAccountProvider, VirtualAccountStatus
from app.models.integration import PaymentGateway
from app.core.config import get_settings

Base.metadata.create_all(bind=engine)
settings = get_settings()

@pytest.fixture
def client():
    return TestClient(app)

def test_billstack_safehaven_generation():
    db = SessionLocal()
    try:
        email = f"test_billstack_sh_{secrets.token_hex(4)}@example.com"
        user = User(
            email=email,
            hashed_password="pw",
            full_name="Safehaven Test User",
            phone_number="08012345678",
            referral_code=f"REF-{secrets.token_hex(4)}"
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        # Enable billstack
        gw = db.query(PaymentGateway).filter(PaymentGateway.identifier == "billstack").first()
        if not gw:
            gw = PaymentGateway(name="Billstack", identifier="billstack", secret_key="test_billstack_key", is_active=True)
            db.add(gw)
        else:
            gw.is_active = True
            gw.secret_key = "test_billstack_key"
        db.commit()

        # Mock generate_billstack_virtual_account
        def mock_generate(*, email, reference, phone, first_name, last_name, bank, **kwargs):
            if bank == "9PSB":
                return {
                    "status": True,
                    "message": "Account reserved",
                    "data": {
                        "reference": f"ref_9psb_{secrets.token_hex(4)}",
                        "account": [{
                            "account_number": "1234567890",
                            "account_name": f"Mele-{first_name}",
                            "bank_name": "9PSB Bank",
                            "bank_id": "9PSB"
                        }]
                    }
                }
            elif bank == "SAFEHAVEN":
                return {
                    "status": True,
                    "message": "Account reserved",
                    "data": {
                        "reference": f"ref_safehaven_{secrets.token_hex(4)}",
                        "account": [{
                            "account_number": "9876543210",
                            "account_name": f"Mele-{first_name}",
                            "bank_name": "Safehaven MFB",
                            "bank_id": "SAFEHAVEN"
                        }]
                    }
                }
            return {"status": False, "message": "Unknown bank"}

        with patch("app.api.v1.endpoints.wallet.generate_billstack_virtual_account", side_effect=mock_generate):
            from app.api.v1.endpoints.wallet import get_bank_transfer_accounts
            res = get_bank_transfer_accounts(user=user, db=db)
            
            # Verify accounts list contains both 9PSB and Safehaven MFB
            bank_names = [a["bank_name"] for a in res["accounts"]]
            assert "9PSB Bank" in bank_names
            assert "Safehaven MFB" in bank_names

            # Verify saved in VirtualAccount table
            saved = db.query(VirtualAccount).filter(
                VirtualAccount.user_id == user.id,
                VirtualAccount.provider == VirtualAccountProvider.BILLSTACK,
                VirtualAccount.status == VirtualAccountStatus.ACTIVE
            ).all()
            saved_banks = [a.bank_name for a in saved]
            assert "9PSB Bank" in saved_banks
            assert "Safehaven MFB" in saved_banks
    finally:
        db.close()

def test_existing_user_with_9psb_gets_safehaven_auto_generated():
    db = SessionLocal()
    try:
        email = f"test_existing_9psb_{secrets.token_hex(4)}@example.com"
        user = User(
            email=email,
            hashed_password="pw",
            full_name="Existing 9PSB User",
            phone_number="08099887766",
            referral_code=f"REF-{secrets.token_hex(4)}"
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        # Pre-seed existing 9PSB account
        existing_9psb = VirtualAccount(
            user_id=user.id,
            provider=VirtualAccountProvider.BILLSTACK,
            account_number=f"111222{secrets.token_hex(2)}",
            account_name="Existing 9PSB User",
            bank_name="9PSB Bank",
            bank_code="9PSB",
            customer_reference=f"ref_existing_9psb_{user.id}_{secrets.token_hex(4)}",
            reservation_reference=f"res_existing_9psb_{secrets.token_hex(4)}",
            status=VirtualAccountStatus.ACTIVE,
        )
        db.add(existing_9psb)
        db.commit()

        generated_banks = []
        def mock_generate(*, email, reference, phone, first_name, last_name, bank, **kwargs):
            generated_banks.append(bank)
            if bank == "SAFEHAVEN":
                return {
                    "status": True,
                    "message": "Account reserved",
                    "data": {
                        "reference": f"ref_sh_{secrets.token_hex(4)}",
                        "account": [{
                            "account_number": "5556667778",
                            "account_name": f"Mele-{first_name}",
                            "bank_name": "Safehaven MFB",
                            "bank_id": "SAFEHAVEN"
                        }]
                    }
                }
            return {"status": False, "message": "Unexpected bank"}

        with patch("app.api.v1.endpoints.wallet.generate_billstack_virtual_account", side_effect=mock_generate):
            from app.api.v1.endpoints.wallet import get_bank_transfer_accounts
            res = get_bank_transfer_accounts(user=user, db=db)
            
            # Verify 9PSB was NOT re-requested from Billstack, but Safehaven was!
            assert "9PSB" not in generated_banks
            assert "SAFEHAVEN" in generated_banks

            # Verify both accounts are returned to user
            returned_acc_numbers = [a["account_number"] for a in res["accounts"]]
            assert existing_9psb.account_number in returned_acc_numbers
            assert "5556667778" in returned_acc_numbers

            returned_banks = [a["bank_name"] for a in res["accounts"]]
            assert "9PSB Bank" in returned_banks
            assert "Safehaven MFB" in returned_banks
    finally:
        db.close()

