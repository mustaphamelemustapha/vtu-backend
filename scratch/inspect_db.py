import os
import sys

# Add backend root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal
from app.models.agent import RewardCampaign, AgentReward
from app.models.transaction import Transaction
from app.models.user import User

db = SessionLocal()
try:
    print("--- Users ---")
    users = db.query(User).all()
    for u in users:
        print(f"User ID: {u.id}, Name: {u.full_name}, Email: {u.email}, Role: {u.role}")

    print("\n--- Campaigns ---")
    campaigns = db.query(RewardCampaign).all()
    for c in campaigns:
        print(f"ID: {c.id}, Title: {c.title}, Type: {c.campaign_type}, Target: {c.target_metric}={c.target_value}, Active: {c.is_active}, Created: {c.created_at}, Activated: {c.activated_at}")

    print("\n--- Transactions ---")
    txs = db.query(Transaction).all()
    for t in txs:
        print(f"ID: {t.id}, User ID: {t.user_id}, Type: {t.tx_type}, Status: {t.status}, Plan: {t.data_plan_code}, Amount: {t.amount}, Created: {t.created_at}")

finally:
    db.close()
