import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import app
from app.models import User, UserRole, Wallet, Transaction, TransactionStatus, TransactionType
from app.models.agent import RewardCampaign, AgentStat, AgentReward, CampaignType

# Setup SQLite memory database
ENGINE = create_engine(
    "sqlite+pysqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
SessionLocal = sessionmaker(bind=ENGINE, autocommit=False, autoflush=False)
Base.metadata.create_all(bind=ENGINE)


def override_get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


def _auth_headers(user_id: int, role: str):
    token = create_access_token(str(user_id), role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def clean_db():
    # Clear tables before each test
    db = SessionLocal()
    try:
        db.query(AgentReward).delete()
        db.query(AgentStat).delete()
        db.query(RewardCampaign).delete()
        db.query(Transaction).delete()
        db.query(Wallet).delete()
        db.query(User).delete()
        db.commit()
    finally:
        db.close()


def test_admin_campaign_crud():
    db = SessionLocal()
    admin = User(
        email="admin@example.com",
        full_name="Admin User",
        hashed_password="hash",
        role=UserRole.ADMIN,
        is_verified=True,
        referral_code="REFADMIN1",
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)

    headers = _auth_headers(admin.id, "admin")

    # 1. Create a campaign
    campaign_data = {
        "title": "Super Promo",
        "campaign_type": "referral",
        "target_metric": "referrals",
        "target_value": 3.0,
        "reward_amount": 500.0,
        "is_active": True
    }
    response = client.post("/api/v1/admin/agent/campaigns", json=campaign_data, headers=headers)
    assert response.status_code == 200, response.text
    created = response.json()
    assert created["title"] == "Super Promo"
    assert created["campaign_type"] == "referral"
    assert float(created["reward_amount"]) == 500.0
    assert float(created["target_value"]) == 3.0
    campaign_id = created["id"]

    # 2. Get active campaigns
    response = client.get("/api/v1/admin/agent/campaigns", headers=headers)
    assert response.status_code == 200
    res = response.json()
    assert res["total"] == 1
    assert res["items"][0]["id"] == campaign_id

    # 3. Update campaign
    update_data = {
        "title": "Super Promo Updated",
        "reward_amount": 600.0
    }
    response = client.put(f"/api/v1/admin/agent/campaigns/{campaign_id}", json=update_data, headers=headers)
    assert response.status_code == 200
    updated = response.json()
    assert updated["title"] == "Super Promo Updated"
    assert float(updated["reward_amount"]) == 600.0

    # 4. Delete campaign
    response = client.delete(f"/api/v1/admin/agent/campaigns/{campaign_id}", headers=headers)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["status"] == "success"
    assert res_data["action"] == "agent_campaign_delete"

    # Confirm deleted
    response = client.get("/api/v1/admin/agent/campaigns", headers=headers)
    assert response.status_code == 200
    assert response.json()["total"] == 0


def test_admin_agent_stats_and_override():
    db = SessionLocal()
    admin = User(
        email="admin@example.com",
        full_name="Admin User",
        hashed_password="hash",
        role=UserRole.ADMIN,
        is_verified=True,
        referral_code="REFADMIN",
    )
    agent = User(
        email="agent@example.com",
        full_name="Agent User",
        hashed_password="hash",
        role=UserRole.RESELLER,
        is_verified=True,
        referral_code="REFAGENT",
    )
    db.add(admin)
    db.add(agent)
    db.commit()
    db.refresh(admin)
    db.refresh(agent)

    # Add initial agent stat
    stat = AgentStat(
        agent_id=agent.id,
        total_data_mb=1024,
        total_airtime_amount=Decimal("500.00"),
        total_transactions=5,
    )
    db.add(stat)
    db.commit()

    headers = _auth_headers(admin.id, "admin")

    # 1. List agent stats
    response = client.get("/api/v1/admin/agent/stats", headers=headers)
    assert response.status_code == 200
    res = response.json()
    assert res["total"] == 1
    assert res["items"][0]["agent_id"] == agent.id
    assert res["items"][0]["total_data_mb"] == 1024

    # 2. Override agent performance stats
    override_data = {
        "total_data_mb": 2048,
        "total_transactions": 10
    }
    response = client.post(f"/api/v1/admin/agent/stats/{agent.id}/override", json=override_data, headers=headers)
    assert response.status_code == 200, response.text
    overridden = response.json()
    assert overridden["total_data_mb"] == 2048
    assert overridden["total_transactions"] == 10

    # Verify database update
    db.refresh(stat)
    assert stat.total_data_mb == 2048
    assert stat.total_transactions == 10


def test_admin_manual_reward():
    db = SessionLocal()
    admin = User(
        email="admin@example.com",
        full_name="Admin User",
        hashed_password="hash",
        role=UserRole.ADMIN,
        is_verified=True,
        referral_code="REFADMIN2",
    )
    agent = User(
        email="agent@example.com",
        full_name="Agent User",
        hashed_password="hash",
        role=UserRole.RESELLER,
        is_verified=True,
        referral_code="REFAGENT2",
    )
    db.add(admin)
    db.add(agent)
    db.commit()
    db.refresh(admin)
    db.refresh(agent)

    wallet = Wallet(user_id=agent.id, balance=Decimal("100.00"))
    db.add(wallet)
    campaign = RewardCampaign(
        title="Manual Campaign",
        campaign_type=CampaignType.REFERRAL,
        target_metric="referrals",
        target_value=Decimal("1.0"),
        reward_amount=Decimal("1500.00"),
        is_active=True
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)

    headers = _auth_headers(admin.id, "admin")

    # Manually reward agent
    reward_payload = {
        "campaign_id": campaign.id,
        "amount": 1500.0,
        "reason": "Manual performance bonus"
    }
    response = client.post(f"/api/v1/admin/agent/stats/{agent.id}/manual-reward", json=reward_payload, headers=headers)
    assert response.status_code == 200, response.text
    reward_res = response.json()
    assert reward_res["status"] == "credited"
    assert float(reward_res["amount"]) == 1500.0

    # Check that wallet was funded
    db.refresh(wallet)
    assert wallet.balance == Decimal("1600.00")

    # Check that transaction was created
    tx = db.query(Transaction).filter_by(user_id=agent.id, tx_type=TransactionType.WALLET_FUND).first()
    assert tx is not None
    assert tx.amount == Decimal("1500.00")
    assert tx.status == TransactionStatus.SUCCESS
    assert "MANUAL_REWARD_" in tx.reference
