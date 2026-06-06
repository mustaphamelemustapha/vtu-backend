from contextlib import contextmanager
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models import UserRole


class _StubSession:
    def __init__(self, *, current_user, users):
        self._current_user = current_user
        self._users = users

    def query(self, *args, **kwargs):
        # get_current_user -> query(User).filter(...).first()
        # temp_reset_virtual_accounts -> query(User).filter(...).first()
        return self

    def filter(self, *args, **kwargs):
        return self

    def delete(self, *args, **kwargs):
        return 0

    def commit(self):
        pass

    def first(self):
        return self._current_user


@contextmanager
def _client_with_state(*, current_user, users):
    app.dependency_overrides.clear()

    def _override_get_db():
        yield _StubSession(current_user=current_user, users=users)

    app.dependency_overrides[get_db] = _override_get_db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _auth_headers(user_id: str, role: str):
    token = create_access_token(user_id, role)
    return {"Authorization": f"Bearer {token}"}


def test_temp_reset_requires_authentication():
    user = SimpleNamespace(id=1, email="user@example.com", full_name="User", role=UserRole.USER, is_active=True)
    with _client_with_state(current_user=user, users=[user]) as client:
        res = client.get("/api/v1/wallet/temp-reset?email=user@example.com")
    assert res.status_code == 401


def test_temp_reset_rejects_non_admin():
    user = SimpleNamespace(id=1, email="user@example.com", full_name="User", role=UserRole.USER, is_active=True)
    with _client_with_state(current_user=user, users=[user]) as client:
        res = client.get(
            "/api/v1/wallet/temp-reset?email=user@example.com",
            headers=_auth_headers("1", "user")
        )
    assert res.status_code == 403
    assert res.json()["detail"] == "Admin access required"


def test_temp_reset_allows_admin():
    admin = SimpleNamespace(id=1, email="admin@example.com", full_name="Admin", role=UserRole.ADMIN, is_active=True)
    with _client_with_state(current_user=admin, users=[admin]) as client:
        res = client.get(
            "/api/v1/wallet/temp-reset?email=user@example.com",
            headers=_auth_headers("1", "admin")
        )
    assert res.status_code == 200
    assert res.json()["status"] == "success"
