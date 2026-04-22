from decimal import Decimal
from types import SimpleNamespace

from app.services.wallet import credit_wallet, debit_wallet


class _DummyQuery:
    def __init__(self, result=None):
        self.result = result

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self.result


class _DummySession:
    def __init__(self, existing=None):
        self.existing = existing
        self.commits = 0
        self.added = []

    def query(self, *args, **kwargs):
        return _DummyQuery(self.existing)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1

    def refresh(self, obj):
        return obj


class _Wallet(SimpleNamespace):
    pass


def test_credit_wallet_returns_existing_matching_ledger():
    existing = SimpleNamespace(id=1)
    session = _DummySession(existing=existing)
    wallet = _Wallet(id=9, balance=Decimal('100.00'), is_locked=False)

    result = credit_wallet(session, wallet, Decimal('50.00'), 'REF123', 'Wallet funding via Paystack')

    assert result is existing
    assert session.commits == 0
    assert wallet.balance == Decimal('100.00')
    assert session.added == []


def test_debit_wallet_returns_existing_matching_ledger():
    existing = SimpleNamespace(id=1)
    session = _DummySession(existing=existing)
    wallet = _Wallet(id=9, balance=Decimal('100.00'), is_locked=False)

    result = debit_wallet(session, wallet, Decimal('40.00'), 'REF456', 'Data purchase to 08123456789')

    assert result is existing
    assert session.commits == 0
    assert wallet.balance == Decimal('100.00')
    assert session.added == []
