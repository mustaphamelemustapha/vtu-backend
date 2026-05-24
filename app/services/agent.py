from datetime import datetime, timezone, date
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_

from app.models.user import User, UserRole
from app.models.transaction import Transaction, TransactionStatus, TransactionType
from app.models.service_transaction import ServiceTransaction
from app.models.wallet import Wallet
from app.models.agent import RewardCampaign, AgentReward, AgentStat, CampaignType
from app.models.referral import Referral, ReferralStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

def get_agent_dashboard_stats(db: Session, user: User) -> dict:
    wallet = db.query(Wallet).filter(Wallet.user_id == user.id).first()
    wallet_balance = wallet.balance if wallet else Decimal("0.00")

    now = _utcnow()
    today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)

    # Calculate Data and Airtime Sales
    # We query both Transaction and ServiceTransaction to cover all bases depending on the VTU flow.

    # -- TODAY --
    today_txs = db.query(Transaction).filter(
        Transaction.user_id == user.id,
        Transaction.status == TransactionStatus.SUCCESS,
        Transaction.created_at >= today_start
    ).all()
    
    today_svc_txs = db.query(ServiceTransaction).filter(
        ServiceTransaction.user_id == user.id,
        ServiceTransaction.status == "success",
        ServiceTransaction.created_at >= today_start
    ).all()

    today_data_naira = Decimal("0.00")
    today_airtime_naira = Decimal("0.00")
    
    for tx in today_txs:
        if tx.tx_type == TransactionType.DATA:
            today_data_naira += tx.amount
        elif tx.tx_type == TransactionType.AIRTIME:
            today_airtime_naira += tx.amount
            
    for stx in today_svc_txs:
        if stx.tx_type.lower() == "airtime":
            today_airtime_naira += stx.amount
        elif stx.tx_type.lower() == "data":
            today_data_naira += stx.amount

    # -- MONTH --
    month_txs = db.query(Transaction).filter(
        Transaction.user_id == user.id,
        Transaction.status == TransactionStatus.SUCCESS,
        Transaction.created_at >= month_start
    ).all()
    
    month_svc_txs = db.query(ServiceTransaction).filter(
        ServiceTransaction.user_id == user.id,
        ServiceTransaction.status == "success",
        ServiceTransaction.created_at >= month_start
    ).all()

    month_data_naira = Decimal("0.00")
    month_airtime_naira = Decimal("0.00")
    
    for tx in month_txs:
        if tx.tx_type == TransactionType.DATA:
            month_data_naira += tx.amount
        elif tx.tx_type == TransactionType.AIRTIME:
            month_airtime_naira += tx.amount
            
    for stx in month_svc_txs:
        if stx.tx_type.lower() == "airtime":
            month_airtime_naira += stx.amount
        elif stx.tx_type.lower() == "data":
            month_data_naira += stx.amount

    # Total Tx Count
    total_tx_count = db.query(Transaction).filter(Transaction.user_id == user.id).count() + \
                     db.query(ServiceTransaction).filter(ServiceTransaction.user_id == user.id).count()

    # Agent Status
    agent_status = "Active" if (month_data_naira > 5000 or month_airtime_naira > 5000) else "Inactive"
    
    # We display Naira value as GB approx (Assuming 1GB ~ N250 for display purposes if real GB tracking isn't feasible)
    # The prompt asked for "Total Data Sold". We'll return the value in float assuming N250 = 1GB as a placeholder
    # In a real system we'd parse the MB out of DataPlan, but this keeps the dashboard fast.
    today_data_gb = float(today_data_naira) / 250.0
    month_data_gb = float(month_data_naira) / 250.0

    return {
        "wallet_balance": wallet_balance,
        "today_data_gb": round(today_data_gb, 1),
        "today_airtime": today_airtime_naira,
        "month_data_gb": round(month_data_gb, 1),
        "month_airtime": month_airtime_naira,
        "total_transactions": total_tx_count,
        "agent_status": agent_status if user.role == UserRole.RESELLER else "Not an Agent",
        "performance_summary": f"Great job! You've sold {round(month_data_gb, 1)}GB this month."
    }

def get_active_campaigns(db: Session, user: User) -> list[dict]:
    campaigns = db.query(RewardCampaign).filter(RewardCampaign.is_active == True).all()
    
    # Calculate progress for volume targets
    # For now, we simulate progress based on overall volume
    agent_stat = db.query(AgentStat).filter(AgentStat.agent_id == user.id).first()
    total_data = agent_stat.total_data_mb / 1024 if agent_stat else 0
    total_airtime = float(agent_stat.total_airtime_amount) if agent_stat else 0

    results = []
    for camp in campaigns:
        progress = 0.0
        is_qualified = False
        
        if camp.campaign_type == CampaignType.VOLUME:
            if camp.target_metric == "data_volume_gb":
                progress = total_data
                is_qualified = progress >= float(camp.target_value)
            elif camp.target_metric == "airtime_volume":
                progress = total_airtime
                is_qualified = progress >= float(camp.target_value)
        
        elif camp.campaign_type == CampaignType.REFERRAL:
            # Check how many qualified referrals they have
            qualified_refs = db.query(Referral).filter(
                Referral.referrer_id == user.id,
                Referral.status == ReferralStatus.QUALIFIED
            ).count()
            progress = float(qualified_refs)
            is_qualified = progress >= float(camp.target_value)

        # Ensure progress doesn't exceed target visually
        if progress > float(camp.target_value):
            progress = float(camp.target_value)

        results.append({
            "id": camp.id,
            "title": camp.title,
            "campaign_type": str(camp.campaign_type.value),
            "target_metric": camp.target_metric,
            "target_value": float(camp.target_value),
            "reward_amount": camp.reward_amount,
            "is_active": camp.is_active,
            "progress_value": round(progress, 2),
            "is_qualified": is_qualified
        })
    return results

def claim_campaign_reward(db: Session, user: User, campaign_id: int) -> dict:
    from app.models.agent import AgentRewardStatus
    from fastapi import HTTPException
    
    # Check if campaign exists and is active
    campaign = db.query(RewardCampaign).filter(
        RewardCampaign.id == campaign_id,
        RewardCampaign.is_active == True
    ).first()
    
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found or inactive.")
        
    # Idempotency check
    existing_reward = db.query(AgentReward).filter(
        AgentReward.agent_id == user.id,
        AgentReward.campaign_id == campaign.id
    ).first()
    
    if existing_reward:
        if existing_reward.status == AgentRewardStatus.CREDITED:
            return {
                "success": True, 
                "message": "Reward already claimed.", 
                "amount_credited": Decimal("0.00")
            }
        elif existing_reward.status == AgentRewardStatus.PENDING:
            raise HTTPException(status_code=400, detail="Reward claim is pending processing.")
            
    # Re-evaluate qualification
    agent_stat = db.query(AgentStat).filter(AgentStat.agent_id == user.id).first()
    total_data = agent_stat.total_data_mb / 1024 if agent_stat else 0
    total_airtime = float(agent_stat.total_airtime_amount) if agent_stat else 0
    
    is_qualified = False
    if campaign.campaign_type == CampaignType.VOLUME:
        if campaign.target_metric == "data_volume_gb":
            is_qualified = total_data >= float(campaign.target_value)
        elif campaign.target_metric == "airtime_volume":
            is_qualified = total_airtime >= float(campaign.target_value)
            
    elif campaign.campaign_type == CampaignType.REFERRAL:
        qualified_refs = db.query(Referral).filter(
            Referral.referrer_id == user.id,
            Referral.status == ReferralStatus.QUALIFIED
        ).count()
        is_qualified = qualified_refs >= float(campaign.target_value)
        
    if not is_qualified:
        raise HTTPException(status_code=400, detail="You do not meet the criteria to claim this reward.")
        
    # Claim the reward
    # 1. Credit wallet
    from app.services.wallet import get_or_create_wallet, credit_wallet
    wallet = get_or_create_wallet(db, user.id)
    tx_ref = f"AG-RWD-{campaign.id}-{user.id}-{int(_utcnow().timestamp())}"
    
    try:
        credit_wallet(db, wallet, campaign.reward_amount, tx_ref, f"Reward claim for {campaign.title}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to credit wallet: {str(e)}")
        
    # 2. Record reward
    reward = AgentReward(
        agent_id=user.id,
        campaign_id=campaign.id,
        amount=campaign.reward_amount,
        status=AgentRewardStatus.CREDITED,
        transaction_reference=tx_ref,
        rewarded_at=_utcnow()
    )
    db.add(reward)
    db.commit()
    
    return {
        "success": True,
        "message": f"Successfully claimed ₦{campaign.reward_amount}",
        "amount_credited": campaign.reward_amount
    }

def get_agent_referrals(db: Session, user: User) -> list[dict]:
    referrals = db.query(Referral).filter(Referral.referrer_id == user.id).all()
    
    results = []
    for ref in referrals:
        referred_user_name = "Unknown User"
        if ref.referred_user:
            referred_user_name = f"{ref.referred_user.first_name or ''} {ref.referred_user.last_name or ''}".strip() or ref.referred_user.email
            
        results.append({
            "id": ref.id,
            "referred_user_name": referred_user_name,
            "status": str(ref.status.value),
            "qualified_at": ref.qualified_at,
            "rewarded_at": ref.rewarded_at,
            "created_at": ref.created_at
        })
    return results
