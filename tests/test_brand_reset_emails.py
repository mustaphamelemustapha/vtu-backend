from unittest.mock import patch
from fastapi.testclient import TestClient

from app.main import app
from app.core.database import get_db
from app.core.security import create_access_token
from app.models.user import User, UserRole
from app.services.transaction_pin import set_pin
from app.services.email import resolve_brand_meta, _resolve_frontend_base_url


def test_kulloma_forgot_password_and_pin_reset_override_stale_meledata_brand():
    db = next(get_db())
    client = TestClient(app)

    email = "kulloma_reset_brand_test@example.com"
    db.query(User).filter(User.email == email).delete()
    db.commit()

    # Simulate an existing Kulloma Data user whose DB row defaulted to brand="meledata"
    user = User(
        email=email,
        full_name="Kulloma Tester",
        hashed_password="mockpassword",
        role=UserRole.CUSTOMER,
        referral_code="KULLRESET1",
        is_active=True,
        brand="meledata",
    )
    set_pin(user, "1234")
    db.add(user)
    db.commit()
    db.refresh(user)

    try:
        # 1. Forgot password from Kulloma Data origin should send KULLOMA DATA email
        with patch("app.api.v1.endpoints.auth.send_password_reset_otp_email", autospec=True) as mock_otp:
            res = client.post(
                "/api/v1/auth/forgot-password",
                json={"identifier": email},
                headers={"Origin": "https://www.kullomadata.com"},
            )
            assert res.status_code == 200
            mock_otp.assert_called_once()
            _, kwargs = mock_otp.call_args
            assert kwargs.get("brand") == "kullomadata"

        db.refresh(user)
        assert user.brand == "kullomadata"

        # 2. Reset user.brand back to stale "meledata" and test PIN reset from Kulloma Data
        user.brand = "meledata"
        db.commit()

        token = create_access_token(str(user.id), user.role.value)
        with patch("app.api.v1.endpoints.security.send_transaction_pin_reset_email", autospec=True) as mock_pin:
            res = client.post(
                "/api/v1/security/pin/reset-request",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Origin": "https://www.kullomadata.com",
                },
            )
            assert res.status_code == 200
            mock_pin.assert_called_once()
            _, kwargs = mock_pin.call_args
            assert kwargs.get("brand") == "kullomadata"

        # Verify resolved URLs for kullomadata vs meledata
        k_meta = resolve_brand_meta("kullomadata")
        assert k_meta["name"] == "KULLOMA DATA"
        assert _resolve_frontend_base_url(k_meta) == "https://www.kullomadata.com"

        m_meta = resolve_brand_meta("meledata")
        assert m_meta["name"] == "MELE DATA"
        assert _resolve_frontend_base_url(m_meta) == "https://meledata.ng"

        # 3. Verify Mele Data requests still send MELE DATA emails
        with patch("app.api.v1.endpoints.auth.send_password_reset_otp_email", autospec=True) as mock_otp_mele:
            res = client.post(
                "/api/v1/auth/forgot-password",
                json={"identifier": email},
                headers={"Origin": "https://meledata.ng"},
            )
            assert res.status_code == 200
            mock_otp_mele.assert_called_once()
            _, kwargs = mock_otp_mele.call_args
            assert kwargs.get("brand") == "meledata"

        # 4. Verify mobile app phone-number variants (+234, 080..., 80...) find user & send OTP to email
        user.phone_number = "+2348039998877"
        db.commit()
        with patch("app.api.v1.endpoints.auth.send_password_reset_otp_email", autospec=True) as mock_otp_phone:
            res = client.post(
                "/api/v1/auth/forgot-password",
                json={"identifier": "08039998877"},
                headers={"X-App-Brand": "kullomadata"},
            )
            assert res.status_code == 200
            body = res.json()
            assert body.get("masked_email") == "ku***@example.com"
            mock_otp_phone.assert_called_once()
            args, kwargs = mock_otp_phone.call_args
            assert args[0] == email
            assert kwargs.get("brand") == "kullomadata"

            # Verify OTP verification and password reset also work with 080... against +234... DB phone
            db.refresh(user)
            otp_code = user.reset_token
            v_res = client.post(
                "/api/v1/auth/verify-reset-token",
                json={"identifier": "08039998877", "otp": otp_code},
                headers={"X-App-Brand": "kullomadata"},
            )
            assert v_res.status_code == 200
            r_res = client.post(
                "/api/v1/auth/reset-password",
                json={"identifier": "08039998877", "otp": otp_code, "new_password": "NewPassword123!"},
                headers={"X-App-Brand": "kullomadata"},
            )
            assert r_res.status_code == 200
    finally:
        db.query(User).filter(User.email == email).delete()
        db.commit()
