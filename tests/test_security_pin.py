from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import hash_pin, verify_pin
from app.main import app


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
        self.commits = 0

    def query(self, *args, **kwargs):
        return _StubQuery(self._user)

    def commit(self):
        self.commits += 1

    def refresh(self, obj):
        return obj


@contextmanager
def _client_with_user(user):
    app.dependency_overrides.clear()

    def _override_get_db():
        yield _StubSession(user)

    app.dependency_overrides[get_db] = _override_get_db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _auth_headers(user_id: str = "1", role: str = "user"):
    from app.core.security import create_access_token

    token = create_access_token(user_id, role)
    return {"Authorization": f"Bearer {token}"}


def test_pin_setup_hashes_and_clears_security_state():
    user = SimpleNamespace(
        id=1,
        email="user@example.com",
        full_name="User",
        role="user",
        is_active=True,
        pin_hash=None,
        pin_set_at=None,
        pin_failed_attempts=3,
        pin_locked_until=datetime.now(timezone.utc) + timedelta(minutes=15),
        pin_reset_token_hash="tok",
        pin_reset_token_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )

    with _client_with_user(user) as client:
        res = client.post(
            "/api/v1/security/pin/setup",
            headers=_auth_headers("1", "user"),
            json={"pin": "1234", "confirm_pin": "1234"},
        )

    assert res.status_code == 200
    assert res.json()["message"] == "Transaction PIN created successfully"
    assert user.pin_hash and user.pin_hash != "1234"
    assert verify_pin("1234", user.pin_hash)
    assert user.pin_failed_attempts == 0
    assert user.pin_locked_until is None
    assert user.pin_reset_token_hash is None
    assert user.pin_reset_token_expires_at is None


def test_pin_verify_locks_after_failed_attempts():
    user = SimpleNamespace(
        id=1,
        email="user@example.com",
        full_name="User",
        role="user",
        is_active=True,
        pin_hash=hash_pin("1234"),
        pin_set_at=datetime.now(timezone.utc),
        pin_failed_attempts=0,
        pin_locked_until=None,
        pin_reset_token_hash=None,
        pin_reset_token_expires_at=None,
    )

    with _client_with_user(user) as client:
        last = None
        for _ in range(5):
            last = client.post(
                "/api/v1/security/pin/verify",
                headers=_auth_headers("1", "user"),
                json={"pin": "1111"},
            )

    assert last is not None
    assert last.status_code == 423
    assert user.pin_locked_until is not None
    assert user.pin_failed_attempts == 0


def test_pin_change_updates_hash_and_clears_attempts():
    user = SimpleNamespace(
        id=1,
        email="user@example.com",
        full_name="User",
        role="user",
        is_active=True,
        pin_hash=hash_pin("1234"),
        pin_set_at=datetime.now(timezone.utc),
        pin_failed_attempts=2,
        pin_locked_until=None,
        pin_reset_token_hash=None,
        pin_reset_token_expires_at=None,
    )

    with _client_with_user(user) as client:
        res = client.post(
            "/api/v1/security/pin/change",
            headers=_auth_headers("1", "user"),
            json={"current_pin": "1234", "new_pin": "4321", "confirm_pin": "4321"},
        )

    assert res.status_code == 200
    assert res.json()["message"] == "Transaction PIN updated successfully"
    assert verify_pin("4321", user.pin_hash)
    assert user.pin_failed_attempts == 0
    assert user.pin_locked_until is None


def test_pin_reset_flow_sets_token_and_resets_pin():
    user = SimpleNamespace(
        id=1,
        email="user@example.com",
        full_name="User",
        role="user",
        is_active=True,
        pin_hash=hash_pin("1234"),
        pin_set_at=datetime.now(timezone.utc),
        pin_failed_attempts=0,
        pin_locked_until=None,
        pin_reset_token_hash=None,
        pin_reset_token_expires_at=None,
    )

    captured = {}

    from app.api.v1.endpoints import security as security_endpoint

    def _fake_send(email: str, token: str):
        captured["email"] = email
        captured["token"] = token

    original = security_endpoint.send_transaction_pin_reset_email
    security_endpoint.send_transaction_pin_reset_email = _fake_send
    try:
        with _client_with_user(user) as client:
            req = client.post(
                "/api/v1/security/pin/reset-request",
                headers=_auth_headers("1", "user"),
            )

            assert req.status_code == 200
            assert req.json()["message"] == "Reset link sent to your email"
            assert user.pin_reset_token_hash is not None
            assert user.pin_reset_token_expires_at is not None
            assert captured["email"] == "user@example.com"
            assert captured["token"]

            confirm = client.post(
                "/api/v1/security/pin/reset-confirm",
                json={
                    "token": captured["token"],
                    "new_pin": "5678",
                    "confirm_pin": "5678",
                },
            )
        assert confirm.status_code == 200
        assert confirm.json()["message"] == "Transaction PIN reset successfully"
        assert verify_pin("5678", user.pin_hash)
        assert user.pin_reset_token_hash is None
        assert user.pin_reset_token_expires_at is None
    finally:
        security_endpoint.send_transaction_pin_reset_email = original
