import os
import sys
from decimal import Decimal
from datetime import datetime, timezone

# Add backend root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.database import SessionLocal, engine, Base
from app.models.user import User, UserRole
from app.models.data_plan import DataPlan
from app.models.transaction import Transaction, TransactionStatus, TransactionType
from app.models.agent import RewardCampaign, CampaignType
from app.services.agent import get_agent_dashboard_stats, get_active_campaigns

def run_verification():
    # Make sure tables exist in local test.db
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    try:
        # 1. Create a test Reseller agent
        test_email = "reseller_verify@meledata.ng"
        agent = db.query(User).filter(User.email == test_email).first()
        if not agent:
            agent = User(
                email=test_email,
                full_name="Verification Agent",
                role=UserRole.RESELLER,
                is_active=True,
                hashed_password="hashed_placeholder",
                referral_code="VERIFY123"
            )
            db.add(agent)
            db.flush()
        else:
            # Clear old transactions to ensure clean numbers
            db.query(Transaction).filter(Transaction.user_id == agent.id).delete()
            db.query(RewardCampaign).delete()
            db.flush()
        
        # 2. Add an MTN 1GB data plan to database
        plan_code = "smeplug:mtn:1001"
        plan = db.query(DataPlan).filter(DataPlan.plan_code == plan_code).first()
        if not plan:
            plan = DataPlan(
                network="mtn",
                plan_code=plan_code,
                plan_name="MTN SME 1GB",
                data_size="1GB",
                validity="30 Days",
                base_price=Decimal("400.00"),
                is_active=True,
                provider="smeplug"
            )
            db.add(plan)
            db.flush()

        # 3. Create a active 10GB Volume Target campaign
        from datetime import timedelta
        campaign = RewardCampaign(
            title="10GB Data Sale Challenge",
            campaign_type=CampaignType.VOLUME,
            target_metric="data_volume_gb",
            target_value=Decimal("10.0"),
            reward_amount=Decimal("1000.00"),
            is_active=True,
            activated_at=datetime.now(timezone.utc) - timedelta(seconds=10)
        )
        db.add(campaign)
        db.flush()

        # 4. Simulate the Reseller buying 1GB of MTN Data (costs N400)
        import uuid
        tx = Transaction(
            user_id=agent.id,
            reference=f"TX_VERIFY_{uuid.uuid4().hex[:8].upper()}",
            network="mtn",
            data_plan_code=plan_code,
            amount=Decimal("400.00"),
            status=TransactionStatus.SUCCESS,
            tx_type=TransactionType.DATA,
            provider="smeplug"
        )
        db.add(tx)
        db.commit()
        
        # Refresh to load database-populated created_at
        db.refresh(tx)
        db.refresh(campaign)
        print(f"DEBUG: Campaign Activated At (in DB): {campaign.activated_at} (type: {type(campaign.activated_at)})")
        print(f"DEBUG: Transaction Created At (in DB): {tx.created_at} (type: {type(tx.created_at)})")

        # 5. Fetch Dashboard Stats and Campaign Progress
        stats = get_agent_dashboard_stats(db, agent)
        campaigns = get_active_campaigns(db, agent)

        print("\n================ VERIFICATION RESULTS ================")
        print(f"Data Sold Today: {stats['today_data_gb']} GB")
        print(f"Data Sold This Month: {stats['month_data_gb']} GB")
        
        for c in campaigns:
            progress_percent = (c['progress_value'] / c['target_value']) * 100
            print(f"\nCampaign: '{c['title']}'")
            print(f"  Target: {c['target_value']} GB")
            print(f"  Current Progress: {c['progress_value']} GB ({progress_percent:.0f}%)")
            
        print("======================================================")

        # Assert correct calculation behavior
        assert stats['today_data_gb'] == 1.0, f"Expected 1.0 GB today, got {stats['today_data_gb']}"
        assert campaigns[0]['progress_value'] == 1.0, f"Expected campaign progress 1.0 GB, got {campaigns[0]['progress_value']}"
        print("SUCCESS! Verification passed. Values are computed 100% correctly.")

    except AssertionError as ae:
        print(f"FAILURE! {ae}")
    finally:
        db.close()

if __name__ == "__main__":
    run_verification()
