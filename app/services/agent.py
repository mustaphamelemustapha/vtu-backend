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

import re

def _parse_size_gb(size_str: str | None) -> float:
    if not size_str:
        return 0.0
    try:
        s = str(size_str).strip().upper()
        match = re.search(r"(\d+(?:\.\d+)?)\s*(GB|MB)", s)
        if not match:
            return 0.0
        val = float(match.group(1))
        unit = match.group(2)
        return val if unit == "GB" else val / 1024.0
    except Exception:
        return 0.0

def get_agent_dashboard_stats(db: Session, user: User) -> dict:
    wallet = db.query(Wallet).filter(Wallet.user_id == user.id).first()
    wallet_balance = wallet.balance if wallet else Decimal("0.00")

    now = _utcnow()
    today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)

    # If database is SQLite, strip timezone info for comparisons
    try:
        is_sqlite = db.get_bind().dialect.name == "sqlite"
    except Exception:
        is_sqlite = True

    if is_sqlite:
        today_start = today_start.replace(tzinfo=None)
        month_start = month_start.replace(tzinfo=None)

    # Calculate Data and Airtime Sales
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
    
    # Map exact plan sizes to calculate real GB sold
    from app.models.data_plan import DataPlan
    all_plans = db.query(DataPlan).all()
    plan_map = {p.plan_code: _parse_size_gb(p.data_size) for p in all_plans}

    today_data_gb = 0.0
    for tx in today_txs:
        if tx.tx_type == TransactionType.DATA:
            gb = plan_map.get(tx.data_plan_code, None)
            if gb is None and tx.data_plan_code:
                parts = str(tx.data_plan_code).split(":")
                raw_code = parts[-1]
                for code, size in plan_map.items():
                    if code.split(":")[-1] == raw_code:
                        gb = size
                        break
            if gb is None:
                gb = float(tx.amount) / 400.0  # Fallback to estimation based on N400/GB
            today_data_gb += gb or 0.0

    for stx in today_svc_txs:
        if stx.tx_type.lower() == "data":
            prod_code = getattr(stx, "product_code", "")
            gb = plan_map.get(prod_code, None)
            if gb is None and prod_code:
                parts = str(prod_code).split(":")
                raw_code = parts[-1]
                for code, size in plan_map.items():
                    if code.split(":")[-1] == raw_code:
                        gb = size
                        break
            if gb is None:
                gb = float(stx.amount) / 400.0
            today_data_gb += gb or 0.0

    month_data_gb = 0.0
    for tx in month_txs:
        if tx.tx_type == TransactionType.DATA:
            gb = plan_map.get(tx.data_plan_code, None)
            if gb is None and tx.data_plan_code:
                parts = str(tx.data_plan_code).split(":")
                raw_code = parts[-1]
                for code, size in plan_map.items():
                    if code.split(":")[-1] == raw_code:
                        gb = size
                        break
            if gb is None:
                gb = float(tx.amount) / 400.0
            month_data_gb += gb or 0.0

    for stx in month_svc_txs:
        if stx.tx_type.lower() == "data":
            prod_code = getattr(stx, "product_code", "")
            gb = plan_map.get(prod_code, None)
            if gb is None and prod_code:
                parts = str(prod_code).split(":")
                raw_code = parts[-1]
                for code, size in plan_map.items():
                    if code.split(":")[-1] == raw_code:
                        gb = size
                        break
            if gb is None:
                gb = float(stx.amount) / 400.0
            month_data_gb += gb or 0.0

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

    results = []
    for camp in campaigns:
        progress = 0.0
        is_qualified = False
        
        if camp.campaign_type == CampaignType.VOLUME:
            campaign_start = camp.activated_at or camp.created_at
            
            # Fetch all successful user transactions
            all_txs = db.query(Transaction).filter(
                Transaction.user_id == user.id,
                Transaction.status == TransactionStatus.SUCCESS
            ).all()
            
            all_stxs = db.query(ServiceTransaction).filter(
                ServiceTransaction.user_id == user.id,
                ServiceTransaction.status == "success"
            ).all()
            
            # Normalize campaign start to naive UTC for safe comparison
            camp_time = campaign_start
            if camp_time and camp_time.tzinfo is not None:
                camp_time = camp_time.astimezone(timezone.utc).replace(tzinfo=None)
            
            txs = []
            for tx in all_txs:
                tx_time = tx.created_at
                if tx_time:
                    if tx_time.tzinfo is not None:
                        tx_time = tx_time.astimezone(timezone.utc).replace(tzinfo=None)
                    if camp_time is None or tx_time >= camp_time:
                        txs.append(tx)
                        
            stxs = []
            for stx in all_stxs:
                stx_time = stx.created_at
                if stx_time:
                    if stx_time.tzinfo is not None:
                        stx_time = stx_time.astimezone(timezone.utc).replace(tzinfo=None)
                    if camp_time is None or stx_time >= camp_time:
                        stxs.append(stx)
            
            if camp.target_metric in ("data_volume_gb", "data_gb"):
                # Fetch all data plans to map code to exact GB size
                from app.models.data_plan import DataPlan
                all_plans = db.query(DataPlan).all()
                plan_map = {p.plan_code: _parse_size_gb(p.data_size) for p in all_plans}
                
                total_gb = 0.0
                for tx in txs:
                    if tx.tx_type == TransactionType.DATA:
                        gb = plan_map.get(tx.data_plan_code, None)
                        if gb is None and tx.data_plan_code:
                            parts = str(tx.data_plan_code).split(":")
                            raw_code = parts[-1]
                            for code, size in plan_map.items():
                                if code.split(":")[-1] == raw_code:
                                    gb = size
                                    break
                        if gb is None:
                            gb = float(tx.amount) / 400.0
                        total_gb += gb or 0.0
                        
                for stx in stxs:
                    if stx.tx_type.lower() == "data":
                        prod_code = getattr(stx, "product_code", "")
                        if prod_code:
                            gb = plan_map.get(prod_code, None)
                            if gb is None:
                                parts = str(prod_code).split(":")
                                raw_code = parts[-1]
                                for code, size in plan_map.items():
                                    if code.split(":")[-1] == raw_code:
                                        gb = size
                                        break
                            if gb is None:
                                gb = float(stx.amount) / 400.0
                            total_gb += gb or 0.0
                
                progress = total_gb
                is_qualified = progress >= float(camp.target_value)
                
            elif camp.target_metric in ("airtime_volume", "airtime_amount"):
                airtime_naira = Decimal("0.00")
                for tx in txs:
                    if tx.tx_type == TransactionType.AIRTIME:
                        airtime_naira += tx.amount
                for stx in stxs:
                    if stx.tx_type.lower() == "airtime":
                        airtime_naira += stx.amount
                        
                progress = float(airtime_naira)
                is_qualified = progress >= float(camp.target_value)
                
            elif camp.target_metric == "transactions":
                progress = float(len(txs) + len(stxs))
                is_qualified = progress >= float(camp.target_value)
        
        elif camp.campaign_type == CampaignType.REFERRAL:
            # Check how many qualified referrals they have since campaign start
            all_refs = db.query(Referral).filter(
                Referral.referrer_id == user.id,
                Referral.status.in_([ReferralStatus.QUALIFIED, ReferralStatus.REWARDED])
            ).all()
            
            camp_time = camp.created_at
            if camp_time and camp_time.tzinfo is not None:
                camp_time = camp_time.astimezone(timezone.utc).replace(tzinfo=None)
                
            qualified_refs = 0
            for ref in all_refs:
                ref_time = ref.created_at
                if ref_time:
                    if ref_time.tzinfo is not None:
                        ref_time = ref_time.astimezone(timezone.utc).replace(tzinfo=None)
                    if camp_time is None or ref_time >= camp_time:
                        qualified_refs += 1
                        
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
            
    # Re-evaluate qualification based on transactions since campaign activation
    is_qualified = False
    
    if campaign.campaign_type == CampaignType.VOLUME:
        campaign_start = campaign.activated_at or campaign.created_at
        
        # Fetch all successful user transactions
        all_txs = db.query(Transaction).filter(
            Transaction.user_id == user.id,
            Transaction.status == TransactionStatus.SUCCESS
        ).all()
        
        all_stxs = db.query(ServiceTransaction).filter(
            ServiceTransaction.user_id == user.id,
            ServiceTransaction.status == "success"
        ).all()
        
        # Normalize campaign start to naive UTC for safe comparison
        camp_time = campaign_start
        if camp_time and camp_time.tzinfo is not None:
            camp_time = camp_time.astimezone(timezone.utc).replace(tzinfo=None)
        
        txs = []
        for tx in all_txs:
            tx_time = tx.created_at
            if tx_time:
                if tx_time.tzinfo is not None:
                    tx_time = tx_time.astimezone(timezone.utc).replace(tzinfo=None)
                if camp_time is None or tx_time >= camp_time:
                    txs.append(tx)
                    
        stxs = []
        for stx in all_stxs:
            stx_time = stx.created_at
            if stx_time:
                if stx_time.tzinfo is not None:
                    stx_time = stx_time.astimezone(timezone.utc).replace(tzinfo=None)
                if camp_time is None or stx_time >= camp_time:
                    stxs.append(stx)
        
        if campaign.target_metric in ("data_volume_gb", "data_gb"):
            from app.models.data_plan import DataPlan
            all_plans = db.query(DataPlan).all()
            plan_map = {p.plan_code: _parse_size_gb(p.data_size) for p in all_plans}
            
            total_gb = 0.0
            for tx in txs:
                if tx.tx_type == TransactionType.DATA:
                    gb = plan_map.get(tx.data_plan_code, None)
                    if gb is None and tx.data_plan_code:
                        parts = str(tx.data_plan_code).split(":")
                        raw_code = parts[-1]
                        for code, size in plan_map.items():
                            if code.split(":")[-1] == raw_code:
                                gb = size
                                break
                    if gb is None:
                        gb = float(tx.amount) / 250.0
                    total_gb += gb or 0.0
                    
            for stx in stxs:
                if stx.tx_type.lower() == "data":
                    prod_code = getattr(stx, "product_code", "")
                    if prod_code:
                        gb = plan_map.get(prod_code, None)
                        if gb is None:
                            parts = str(prod_code).split(":")
                            raw_code = parts[-1]
                            for code, size in plan_map.items():
                                if code.split(":")[-1] == raw_code:
                                    gb = size
                                    break
                        if gb is None:
                            gb = float(stx.amount) / 250.0
                        total_gb += gb or 0.0
                    
            progress = total_gb
            is_qualified = progress >= float(campaign.target_value)
            
        elif campaign.target_metric in ("airtime_volume", "airtime_amount"):
            airtime_naira = Decimal("0.00")
            for tx in txs:
                if tx.tx_type == TransactionType.AIRTIME:
                    airtime_naira += tx.amount
            for stx in stxs:
                if stx.tx_type.lower() == "airtime":
                    airtime_naira += stx.amount
                    
            progress = float(airtime_naira)
            is_qualified = progress >= float(campaign.target_value)
            
        elif campaign.target_metric == "transactions":
            progress = float(len(txs) + len(stxs))
            is_qualified = progress >= float(campaign.target_value)
            
    elif campaign.campaign_type == CampaignType.REFERRAL:
        all_refs = db.query(Referral).filter(
            Referral.referrer_id == user.id,
            Referral.status.in_([ReferralStatus.QUALIFIED, ReferralStatus.REWARDED])
        ).all()
        
        camp_time = campaign.created_at
        if camp_time and camp_time.tzinfo is not None:
            camp_time = camp_time.astimezone(timezone.utc).replace(tzinfo=None)
            
        qualified_refs = 0
        for ref in all_refs:
            ref_time = ref.created_at
            if ref_time:
                if ref_time.tzinfo is not None:
                    ref_time = ref_time.astimezone(timezone.utc).replace(tzinfo=None)
                if camp_time is None or ref_time >= camp_time:
                    qualified_refs += 1
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
        
    # 1b. Create Transaction record
    tx = Transaction(
        user_id=user.id,
        reference=tx_ref,
        amount=campaign.reward_amount,
        status=TransactionStatus.SUCCESS,
        tx_type=TransactionType.WALLET_FUND,
        failure_reason="Agent Reward",
    )
    db.add(tx)

    # 2. Record reward
    reward = AgentReward(
        agent_id=user.id,
        campaign_id=campaign.id,
        amount=campaign.reward_amount,
        status=AgentRewardStatus.CREDITED,
        transaction_reference=tx_ref,
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
