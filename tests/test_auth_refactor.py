from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.database import get_db
from app.models.user import User, UserRole
from app.schemas.auth import validate_nigerian_state, RegisterRequest, UpdateMeRequest
from app.api.v1.endpoints.auth import _normalize_phone


def test_validate_nigerian_state():
    assert validate_nigerian_state("Lagos") == "Lagos"
    assert validate_nigerian_state("lagos") == "Lagos"
    assert validate_nigerian_state("LAGOS") == "Lagos"
    assert validate_nigerian_state("FCT") == "FCT"
    assert validate_nigerian_state("fct") == "FCT"
    assert validate_nigerian_state("Abuja") == "FCT"
    assert validate_nigerian_state("Kano") == "Kano"
    assert validate_nigerian_state(None) is None
    assert validate_nigerian_state("") is None

    with pytest.raises(ValueError):
        validate_nigerian_state("London")

    with pytest.raises(ValueError):
        validate_nigerian_state("Texas")


def test_normalize_phone():
    assert _normalize_phone("08012345678") == "08012345678"
    assert _normalize_phone("+2348012345678") == "08012345678"
    assert _normalize_phone("2348012345678") == "08012345678"
    assert _normalize_phone("+234 801 234 5678") == "08012345678"
    assert _normalize_phone("8012345678") == "08012345678"
    assert _normalize_phone(None) is None
    assert _normalize_phone("") is None


def test_register_request_schema_validation():
    # Valid with state and pin
    req = RegisterRequest(
        email="test@example.com",
        full_name="Test User",
        phone_number="08012345678",
        password="Password123!",
        state="kano",
        pin="1234",
    )
    assert req.state == "Kano"
    assert req.pin == "1234"

    # Valid without state and pin (backward compatibility)
    req2 = RegisterRequest(
        email="test2@example.com",
        full_name="Test User 2",
        password="Password123!",
    )
    assert req2.state is None
    assert req2.pin is None

    # Invalid state
    with pytest.raises(ValueError):
        RegisterRequest(
            email="test3@example.com",
            full_name="Test User 3",
            password="Password123!",
            state="NotAState",
        )

    # Invalid PIN (not 4 digits)
    with pytest.raises(ValueError):
        RegisterRequest(
            email="test4@example.com",
            full_name="Test User 4",
            password="Password123!",
            pin="12345",
        )


def test_lookup_endpoint_returns_only_exists():
    client = TestClient(app)
    mock_db = MagicMock()
    app.dependency_overrides[get_db] = lambda: mock_db

    try:
        # 1. User doesn't exist
        mock_db.query.return_value.filter.return_value.first.return_value = None
        res1 = client.post("/api/v1/auth/lookup", json={"identifier": "08099999999"})
        assert res1.status_code == 200
        assert res1.json() == {"exists": False}

        # 2. User exists -> must ONLY return {"exists": True}
        fake_user = User(
            id=1,
            email="secret@example.com",
            phone_number="08012345678",
            full_name="Sensitive Name",
            hashed_password="...",
            role=UserRole.CUSTOMER,
        )
        mock_db.query.return_value.filter.return_value.first.return_value = fake_user
        res2 = client.post("/api/v1/auth/lookup", json={"identifier": "08012345678"})
        assert res2.status_code == 200
        assert res2.json() == {"exists": True}
        assert "email" not in res2.json()
        assert "full_name" not in res2.json()
        assert "phone_number" not in res2.json()
    finally:
        app.dependency_overrides.clear()
