from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models import UserRole, TransactionStatus, TransactionType


class _StubQuery:
    def __init__(self, *, all_results, first_result=None):
        self._all = list(all_results)
        self._first = first_result
        self._offset = 0
        self._limit = None

    def join(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def offset(self, value):
        self._offset = int(value or 0)
        return self

    def limit(self, value):
        self._limit = int(value) if value is not None else None
        return self

    def count(self):
        return len(self._all)

    def first(self):
        return self._first

    def all(self):
        start = self._offset
        end = None if self._limit is None else start + self._limit
        return self._all[start:end]


class _StubSession:
    def __init__(self, *, current_user, users, tx_rows):
        self._current_user = current_user
        self._users = users
        self._tx_rows = tx_rows

    def query(self, *args, **kwargs):
        # get_current_user -> query(User).filter(...).first()
        if args and getattr(args[0], "__name__", "") == "User":
            return _StubQuery(all_results=self._users, first_result=self._current_user)
        # admin transactions -> query(Transaction, User.email.label(...)).join(...).all()
        if args and getattr(args[0], "__name__", "") == "Transaction":
            return _StubQuery(all_results=self._tx_rows, first_result=None)
        return _StubQuery(all_results=[], first_result=None)

    def commit(self):
        return None

    def refresh(self, obj):
        return obj


@contextmanager
def _client_with_state(*, current_user, users, tx_rows):
    app.dependency_overrides.clear()

    def _override_get_db():
        yield _StubSession(current_user=current_user, users=users, tx_rows=tx_rows)

    app.dependency_overrides[get_db] = _override_get_db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _auth_headers(user_id: str, role: str):
    token = create_access_token(user_id, role)
    return {"Authorization": f"Bearer {token}"}


def test_admin_transactions_requires_admin():
    user = SimpleNamespace(id=1, email="user@example.com", full_name="User", role=UserRole.USER, is_active=True)
    with _client_with_state(current_user=user, users=[user], tx_rows=[]) as client:
        res = client.get("/api/v1/admin/transactions", headers=_auth_headers("1", "user"))
    assert res.status_code == 403
    assert res.json()["detail"] == "Admin access required"


def test_admin_transactions_lists_rows():
    admin = SimpleNamespace(id=1, email="admin@example.com", full_name="Admin", role=UserRole.ADMIN, is_active=True)
    tx = SimpleNamespace(
        id=10,
        created_at=datetime(2026, 2, 14, 12, 0, tzinfo=timezone.utc),
        user_id=2,
        reference="TX_1",
        tx_type=TransactionType.DATA,
        amount=Decimal("500.00"),
        status=TransactionStatus.SUCCESS,
        network="mtn",
        data_plan_code="1001",
        external_reference="EXT_1",
        failure_reason=None,
    )
    rows = [(tx, "user@example.com")]
    with _client_with_state(current_user=admin, users=[admin], tx_rows=rows) as client:
        res = client.get("/api/v1/admin/transactions", headers=_auth_headers("1", "admin"))
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["reference"] == "TX_1"
    assert body["items"][0]["user_email"] == "user@example.com"


def test_admin_users_lists_users():
    admin = SimpleNamespace(id=1, email="admin@example.com", full_name="Admin", role=UserRole.ADMIN, is_active=True)
    u1 = SimpleNamespace(
        id=2,
        created_at=datetime(2026, 2, 14, 12, 0, tzinfo=timezone.utc),
        email="user@example.com",
        full_name="User",
        role=UserRole.USER,
        is_active=True,
        is_verified=False,
    )
    with _client_with_state(current_user=admin, users=[u1], tx_rows=[]) as client:
        res = client.get("/api/v1/admin/users", headers=_auth_headers("1", "admin"))
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    assert body["items"][0]["email"] == "user@example.com"


def test_admin_pricing_endpoints_require_admin():
    user = SimpleNamespace(id=1, email="user@example.com", full_name="User", role=UserRole.USER, is_active=True)
    with _client_with_state(current_user=user, users=[user], tx_rows=[]) as client:
        list_res = client.get("/api/v1/admin/pricing", headers=_auth_headers("1", "user"))
        update_res = client.post(
            "/api/v1/admin/pricing",
            headers=_auth_headers("1", "user"),
            json={"network": "mtn", "role": "user", "margin": 10},
        )

    assert list_res.status_code == 403
    assert update_res.status_code == 403
    assert list_res.json()["detail"] == "Admin access required"
    assert update_res.json()["detail"] == "Admin access required"
