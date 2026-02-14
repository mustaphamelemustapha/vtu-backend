from contextlib import contextmanager
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import create_access_token, hash_password, verify_password
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
    token = create_access_token(user_id, role)
    return {"Authorization": f"Bearer {token}"}


def test_update_me_updates_full_name():
    user = SimpleNamespace(
        id=1,
        email="user@example.com",
        full_name="Old Name",
        role=UserRole.USER,
        is_active=True,
        is_verified=True,
        hashed_password=hash_password("Password123!"),
        reset_token=None,
        reset_token_expires_at=None,
    )

    with _client_with_user(user) as client:
        res = client.patch("/api/v1/auth/me", headers=_auth_headers("1", "user"), json={"full_name": "New Name"})

    assert res.status_code == 200
    assert res.json()["full_name"] == "New Name"


def test_change_password_rejects_wrong_current_password():
    user = SimpleNamespace(
        id=1,
        email="user@example.com",
        full_name="User",
        role=UserRole.USER,
        is_active=True,
        is_verified=True,
        hashed_password=hash_password("Password123!"),
        reset_token=None,
        reset_token_expires_at=None,
    )

    with _client_with_user(user) as client:
        res = client.post(
            "/api/v1/auth/change-password",
            headers=_auth_headers("1", "user"),
            json={"current_password": "WrongPass!", "new_password": "NewPassword123!"},
        )

    assert res.status_code == 400
    assert res.json()["detail"] == "Current password is incorrect"


def test_change_password_updates_hash():
    user = SimpleNamespace(
        id=1,
        email="user@example.com",
        full_name="User",
        role=UserRole.USER,
        is_active=True,
        is_verified=True,
        hashed_password=hash_password("Password123!"),
        reset_token="tok",
        reset_token_expires_at="anything",
    )

    with _client_with_user(user) as client:
        res = client.post(
            "/api/v1/auth/change-password",
            headers=_auth_headers("1", "user"),
            json={"current_password": "Password123!", "new_password": "NewPassword123!"},
        )

    assert res.status_code == 200
    assert res.json()["message"] == "Password updated successfully"
    assert verify_password("NewPassword123!", user.hashed_password)
    assert user.reset_token is None
    assert user.reset_token_expires_at is None

