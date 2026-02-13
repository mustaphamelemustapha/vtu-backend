from contextlib import contextmanager
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.database import get_db
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


def test_forgot_password_does_not_enumerate_user():
    with _client_with_user(None) as client:
        res = client.post("/api/v1/auth/forgot-password", json={"email": "nobody@example.com"})

    assert res.status_code == 200
    body = res.json()
    assert body["message"] == "If the email exists, a reset token has been generated"
    assert body.get("reset_token") is None


def test_forgot_password_returns_token_in_non_production():
    # tests run with ENVIRONMENT=test, so non-production behavior should include a token.
    user = SimpleNamespace(email="user@example.com", reset_token=None, reset_token_expires_at=None)
    with _client_with_user(user) as client:
        res = client.post("/api/v1/auth/forgot-password", json={"email": "user@example.com"})

    assert res.status_code == 200
    body = res.json()
    assert body["message"] == "If the email exists, a reset token has been generated"
    assert isinstance(body.get("reset_token"), str)
    assert len(body["reset_token"]) > 10

