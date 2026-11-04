import pytest
import jwt
from types import SimpleNamespace
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import hash_pin, decode_token, settings
from app.main import app
from app.models import UserRole


class _StubQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._result


class _StubSession:
    def __init__(self, user):
        self._user = user

    def query(self, *args, **kwargs):
        return _StubQuery(self._user)

    def commit(self):
        pass

    def rollback(self):
        pass


def test_pin_login_success():
    user = SimpleNamespace(
        id=42,
        email="test@meledata.ng",
        phone_number="08012345678",
        is_active=True,
        role=UserRole.CUSTOMER,
        pin_hash=hash_pin("1234"),
        pin_failed_attempts=0,
        pin_locked_until=None,
    )

    def _override_get_db():
        yield _StubSession(user)

    app.dependency_overrides[get_db] = _override_get_db
    try:
        with TestClient(app) as client:
            resp = client.post("/api/v1/auth/pin-login", json={"identifier": "test@meledata.ng", "pin": "1234"})
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert "access_token" in data
            assert data["token_type"] == "bearer"

            decoded = decode_token(data["access_token"])
            assert decoded["sub"] == "42"
            assert decoded["type"] == "access"
    finally:
        app.dependency_overrides.clear()


def test_pin_login_wrong_pin():
    user = SimpleNamespace(
        id=42,
        email="test@meledata.ng",
        phone_number="08012345678",
        is_active=True,
        role=UserRole.CUSTOMER,
        pin_hash=hash_pin("1234"),
        pin_failed_attempts=0,
        pin_locked_until=None,
    )

    def _override_get_db():
        yield _StubSession(user)

    app.dependency_overrides[get_db] = _override_get_db
    try:
        with TestClient(app) as client:
            resp = client.post("/api/v1/auth/pin-login", json={"identifier": "test@meledata.ng", "pin": "9999"})
            assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_legacy_secret_key_decode():
    old_secret = "legacy-render-secret-key-12345"
    settings.legacy_secret_keys = f"other-key,{old_secret}"

    payload = {"sub": "99", "role": "customer", "type": "access", "exp": 9999999999}
    token = jwt.encode(payload, old_secret, algorithm="HS256")

    decoded = decode_token(token)
    assert decoded["sub"] == "99"
    assert decoded["type"] == "access"
