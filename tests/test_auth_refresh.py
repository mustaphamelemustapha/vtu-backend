from contextlib import contextmanager
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import create_access_token, create_refresh_token, decode_token
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


def test_refresh_rejects_access_token():
    user = SimpleNamespace(id=1, is_active=True, role=UserRole.USER)

    access = create_access_token("1", "user")
    with _client_with_user(user) as client:
        res = client.post("/api/v1/auth/refresh", json={"refresh_token": access})

    assert res.status_code == 401
    assert res.json()["detail"] == "Invalid refresh token"


def test_refresh_rejects_invalid_token():
    user = SimpleNamespace(id=1, is_active=True, role=UserRole.USER)
    with _client_with_user(user) as client:
        res = client.post("/api/v1/auth/refresh", json={"refresh_token": "not-a-jwt"})

    assert res.status_code == 401
    assert res.json()["detail"] == "Invalid refresh token"


def test_refresh_rejects_inactive_user():
    user = SimpleNamespace(id=1, is_active=False, role=UserRole.USER)

    refresh = create_refresh_token("1", "user")
    with _client_with_user(user) as client:
        res = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})

    assert res.status_code == 401
    assert res.json()["detail"] == "User not found or inactive"


def test_refresh_success_returns_new_pair():
    user = SimpleNamespace(id=1, is_active=True, role=UserRole.USER)

    refresh = create_refresh_token("1", "user")
    with _client_with_user(user) as client:
        res = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert res.status_code == 200

    body = res.json()
    assert body["token_type"] == "bearer"
    assert "access_token" in body
    assert "refresh_token" in body

    decoded_access = decode_token(body["access_token"])
    assert decoded_access["type"] == "access"
    assert decoded_access["sub"] == "1"

    decoded_refresh = decode_token(body["refresh_token"])
    assert decoded_refresh["type"] == "refresh"
    assert decoded_refresh["sub"] == "1"
