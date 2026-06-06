from datetime import datetime, timezone, timedelta
from decimal import Decimal
from contextlib import contextmanager
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.database import get_db
from app.main import app
from app.models import UserRole, CampaignType, RewardCampaign
from app.services.agent import get_active_campaigns


class _StubQuery:
    def __init__(self, *, all_results):
        self._all = list(all_results)

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return self._all

    def count(self):
        return len(self._all)


class _StubSession:
    def __init__(self, *, campaigns, transactions):
        self._campaigns = campaigns
        self._transactions = transactions

    def query(self, model, *args, **kwargs):
        if model == RewardCampaign:
            return _StubQuery(all_results=self._campaigns)
        return _StubQuery(all_results=self._transactions)


def test_get_active_campaigns_filters_by_activation_time():
    now = datetime.now(timezone.utc)
    
    # Campaign activated 1 hour ago
    camp = SimpleNamespace(
        id=1,
        title="Volume Promo",
        campaign_type=CampaignType.VOLUME,
        target_metric="data_volume_gb",
        target_value=Decimal("50.00"),
        reward_amount=Decimal("1000.00"),
        is_active=True,
        created_at=now - timedelta(hours=5),
        activated_at=now - timedelta(hours=1)
    )
    
    # Transaction created 2 hours ago (before activation)
    tx_before = SimpleNamespace(
        id=10,
        user_id=1,
        tx_type="data",
        amount=Decimal("250.00"),
        status="success",
        data_plan_code="plan_1",
        created_at=now - timedelta(hours=2)
    )
    
    # Transaction created 30 mins ago (after activation)
    tx_after = SimpleNamespace(
        id=11,
        user_id=1,
        tx_type="data",
        amount=Decimal("500.00"),
        status="success",
        data_plan_code="plan_2",
        created_at=now - timedelta(minutes=30)
    )
    
    # Mock plans mapping
    plan_1 = SimpleNamespace(plan_code="plan_1", data_size="10GB")
    plan_2 = SimpleNamespace(plan_code="plan_2", data_size="20GB")
    
    class MockSession(_StubSession):
        def query(self, model, *args, **kwargs):
            if model == RewardCampaign:
                return _StubQuery(all_results=[camp])
            # Handle DataPlan queries
            from app.models.data_plan import DataPlan
            if model == DataPlan:
                return _StubQuery(all_results=[plan_1, plan_2])
            # We filter transactions manually in the mock to simulate sql filter >= campaign_start
            # campaign_start is activated_at (now - 1 hour)
            # Only tx_after is >= campaign_start
            return _StubQuery(all_results=[tx_after])

    db = MockSession(campaigns=[camp], transactions=[tx_before, tx_after])
    user = SimpleNamespace(id=1, email="agent@example.com", role=UserRole.RESELLER)
    
    results = get_active_campaigns(db, user)
    assert len(results) == 1
    # Only plan_2 (20GB) was purchased after campaign activation
    assert results[0]["progress_value"] == 20.0
