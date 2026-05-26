from decimal import Decimal
from types import SimpleNamespace
import pytest
from fastapi import HTTPException

from app.services.wallet import credit_wallet, debit_wallet


class _DummyQuery:
    def __init__(self, result=None):
        self.result = result
        self.called_with_for_update = False

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def with_for_update(self, *args, **kwargs):
        self.called_with_for_update = True
        return self

    def first(self):
        return self.result


class _DummySession:
    def __init__(self, wallet=None, existing=None):
        self.wallet = wallet
        self.existing = existing
        self.commits = 0
        self.flushes = 0
        self.added = []
        self.queries = []

    def query(self, model_class, *args, **kwargs):
        # Return either the wallet or the existing ledger based on the queried model name
        if model_class.__name__ == "Wallet":
            q = _DummyQuery(self.wallet)
        else:
            q = _DummyQuery(self.existing)
        self.queries.append(q)
        return q

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1

    def flush(self):
        self.flushes += 1

    def refresh(self, obj):
        return obj


class _Wallet(SimpleNamespace):
    pass


def test_credit_wallet_returns_existing_matching_ledger():
    existing = SimpleNamespace(id=1)
    wallet = _Wallet(id=9, balance=Decimal('100.00'), is_locked=False)
    session = _DummySession(wallet=wallet, existing=existing)

    result = credit_wallet(session, wallet, Decimal('50.00'), 'REF123', 'Wallet funding via Paystack')

    assert result is existing
    assert session.commits == 0
    assert wallet.balance == Decimal('100.00')
    assert session.added == []


def test_debit_wallet_returns_existing_matching_ledger():
    existing = SimpleNamespace(id=1)
    wallet = _Wallet(id=9, balance=Decimal('100.00'), is_locked=False)
    session = _DummySession(wallet=wallet, existing=existing)

    result = debit_wallet(session, wallet, Decimal('40.00'), 'REF456', 'Data purchase to 08123456789')

    assert result is existing
    assert session.commits == 0
    assert wallet.balance == Decimal('100.00')
    assert session.added == []


def test_credit_wallet_success():
    wallet = _Wallet(id=9, balance=Decimal('100.00'), is_locked=False)
    session = _DummySession(wallet=wallet, existing=None)

    result = credit_wallet(session, wallet, Decimal('50.00'), 'REF123', 'Wallet funding via Paystack')

    assert result is not None
    assert wallet.balance == Decimal('150.00')
    assert session.commits == 1
    assert len(session.added) == 1
    assert session.added[0].amount == Decimal('50.00')
    # Verify pessimistic locking query was called
    assert any(q.called_with_for_update for q in session.queries)


def test_debit_wallet_success():
    wallet = _Wallet(id=9, balance=Decimal('100.00'), is_locked=False)
    session = _DummySession(wallet=wallet, existing=None)

    result = debit_wallet(session, wallet, Decimal('40.00'), 'REF456', 'Data purchase to 08123456789')

    assert result is not None
    assert wallet.balance == Decimal('60.00')
    assert session.commits == 1
    assert len(session.added) == 1
    assert session.added[0].amount == Decimal('40.00')
    assert any(q.called_with_for_update for q in session.queries)


def test_debit_wallet_insufficient_balance():
    wallet = _Wallet(id=9, balance=Decimal('30.00'), is_locked=False)
    session = _DummySession(wallet=wallet, existing=None)

    with pytest.raises(HTTPException) as excinfo:
        debit_wallet(session, wallet, Decimal('40.00'), 'REF456', 'Data purchase to 08123456789')

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "Insufficient balance"
    assert wallet.balance == Decimal('30.00')
    assert session.commits == 0
    assert len(session.added) == 0


def test_wallet_locked_raises_exception():
    wallet = _Wallet(id=9, balance=Decimal('100.00'), is_locked=True)
    session = _DummySession(wallet=wallet, existing=None)

    with pytest.raises(HTTPException) as excinfo:
        debit_wallet(session, wallet, Decimal('40.00'), 'REF456', 'Data purchase')

    assert excinfo.value.status_code == 423
    assert excinfo.value.detail == "Wallet is locked"

    with pytest.raises(HTTPException) as excinfo:
        credit_wallet(session, wallet, Decimal('40.00'), 'REF456', 'Funding')

    assert excinfo.value.status_code == 423
    assert excinfo.value.detail == "Wallet is locked"
