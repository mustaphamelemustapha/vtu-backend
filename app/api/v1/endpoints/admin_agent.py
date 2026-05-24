from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import or_
from decimal import Decimal
from datetime import datetime
from typing import Optional

from app.core.database import get_db
from app.dependencies import require_admin
from app.models import (
    User,
    UserRole,
    AdminAuditLog,
    Transaction,
    TransactionStatus,
    TransactionType,
    RewardCampaign,
    CampaignType,
    AgentReward,
    AgentRewardStatus,
    AgentStat,
)
from app.services.wallet import get_or_create_wallet, credit_wallet
from app.schemas.admin_agent import (
    RewardCampaignCreate,
    RewardCampaignUpdate,
    RewardCampaignOut,
    RewardCampaignsResponse,
    AgentStatOut,
    AgentStatsResponse,
    AgentStatOverride,
    ManualRewardRequest,
    AgentRewardOut,
    AgentRewardsResponse,
)

router = APIRouter()


@router.get("/campaigns", response_model=RewardCampaignsResponse)
def list_campaigns(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    page: int = 1,
    page_size: int = 50,
):
    if page < 1:
        raise HTTPException(status_code=400, detail="page must be >= 1")
    if page_size < 1 or page_size > 200:
        raise HTTPException(status_code=400, detail="page_size must be between 1 and 200")

    query = db.query(RewardCampaign)
    total = query.count()
    campaigns = query.order_by(RewardCampaign.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": campaigns, "total": total}


@router.post("/campaigns", response_model=RewardCampaignOut)
def create_campaign(
    payload: RewardCampaignCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    campaign = RewardCampaign(
        title=payload.title,
        campaign_type=payload.campaign_type,
        target_metric=payload.target_metric,
        target_value=payload.target_value,
        reward_amount=payload.reward_amount,
        is_active=payload.is_active,
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)

    db.add(
        AdminAuditLog(
            admin_email=admin.email,
            action="agent_campaign_create",
            target=str(campaign.id),
            details={
                "title": campaign.title,
                "campaign_type": campaign.campaign_type,
                "target_metric": campaign.target_metric,
                "target_value": str(campaign.target_value),
                "reward_amount": str(campaign.reward_amount),
            },
        )
    )
    db.commit()
    return campaign


@router.get("/campaigns/{campaign_id}", response_model=RewardCampaignOut)
def get_campaign(
    campaign_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    campaign = db.query(RewardCampaign).filter(RewardCampaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


@router.put("/campaigns/{campaign_id}", response_model=RewardCampaignOut)
def update_campaign(
    campaign_id: int,
    payload: RewardCampaignUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    campaign = db.query(RewardCampaign).filter(RewardCampaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    update_data = payload.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(campaign, field, value)
    db.commit()
    db.refresh(campaign)

    # Cast Decimal fields to string for JSON serialization in details
    audit_details = {}
    for k, v in update_data.items():
        if isinstance(v, Decimal):
            audit_details[k] = str(v)
        else:
            audit_details[k] = v

    db.add(
        AdminAuditLog(
            admin_email=admin.email,
            action="agent_campaign_update",
            target=str(campaign.id),
            details=audit_details,
        )
    )
    db.commit()
    return campaign


@router.delete("/campaigns/{campaign_id}")
def delete_campaign(
    campaign_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    campaign = db.query(RewardCampaign).filter(RewardCampaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    rewards_count = db.query(AgentReward).filter(AgentReward.campaign_id == campaign_id).count()
    if rewards_count > 0:
        campaign.is_active = False
        db.commit()
        action = "agent_campaign_deactivate"
        details = {"reason": "Campaign has associated rewards, deactivated instead of deleted"}
    else:
        db.delete(campaign)
        db.commit()
        action = "agent_campaign_delete"
        details = {"reason": "No associated rewards, deleted from database"}

    db.add(
        AdminAuditLog(
            admin_email=admin.email,
            action=action,
            target=str(campaign_id),
            details=details,
        )
    )
    db.commit()
    return {"status": "success", "action": action}


@router.get("/stats", response_model=AgentStatsResponse)
def list_agent_stats(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    q: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
):
    if page < 1:
        raise HTTPException(status_code=400, detail="page must be >= 1")
    if page_size < 1 or page_size > 200:
        raise HTTPException(status_code=400, detail="page_size must be between 1 and 200")

    query = db.query(AgentStat).join(User, AgentStat.agent_id == User.id)
    if q:
        needle = f"%{q.strip()}%"
        query = query.filter(
            or_(
                User.email.ilike(needle),
                User.full_name.ilike(needle),
            )
        )

    total = query.count()
    stats = query.order_by(AgentStat.id.desc()).offset((page - 1) * page_size).limit(page_size).all()

    items = []
    for s in stats:
        items.append(
            {
                "id": s.id,
                "agent_id": s.agent_id,
                "agent_email": s.agent.email,
                "agent_full_name": s.agent.full_name,
                "total_data_mb": s.total_data_mb,
                "total_airtime_amount": s.total_airtime_amount,
                "total_transactions": s.total_transactions,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
            }
        )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/stats/{agent_id}/override", response_model=AgentStatOut)
def override_agent_stats(
    agent_id: int,
    payload: AgentStatOverride,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    agent = db.query(User).filter(User.id == agent_id, User.role == UserRole.RESELLER).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found or is not a reseller")

    stat = db.query(AgentStat).filter(AgentStat.agent_id == agent_id).first()
    if not stat:
        stat = AgentStat(agent_id=agent_id)
        db.add(stat)
        db.flush()

    old_stats = {
        "total_data_mb": stat.total_data_mb,
        "total_airtime_amount": str(stat.total_airtime_amount),
        "total_transactions": stat.total_transactions,
    }

    if payload.total_data_mb is not None:
        stat.total_data_mb = payload.total_data_mb
    if payload.total_airtime_amount is not None:
        stat.total_airtime_amount = payload.total_airtime_amount
    if payload.total_transactions is not None:
        stat.total_transactions = payload.total_transactions

    db.commit()
    db.refresh(stat)

    db.add(
        AdminAuditLog(
            admin_email=admin.email,
            action="agent_stat_override",
            target=str(agent_id),
            details={
                "old": old_stats,
                "new": {
                    "total_data_mb": stat.total_data_mb,
                    "total_airtime_amount": str(stat.total_airtime_amount),
                    "total_transactions": stat.total_transactions,
                },
            },
        )
    )
    db.commit()

    return {
        "id": stat.id,
        "agent_id": stat.agent_id,
        "agent_email": agent.email,
        "agent_full_name": agent.full_name,
        "total_data_mb": stat.total_data_mb,
        "total_airtime_amount": stat.total_airtime_amount,
        "total_transactions": stat.total_transactions,
        "created_at": stat.created_at,
        "updated_at": stat.updated_at,
    }


@router.get("/stats/{agent_id}/rewards", response_model=AgentRewardsResponse)
def get_agent_rewards(
    agent_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    page: int = 1,
    page_size: int = 50,
):
    if page < 1:
        raise HTTPException(status_code=400, detail="page must be >= 1")
    if page_size < 1 or page_size > 200:
        raise HTTPException(status_code=400, detail="page_size must be between 1 and 200")

    agent = db.query(User).filter(User.id == agent_id, User.role == UserRole.RESELLER).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found or is not a reseller")

    query = db.query(AgentReward).filter(AgentReward.agent_id == agent_id)
    total = query.count()
    rewards = query.order_by(AgentReward.id.desc()).offset((page - 1) * page_size).limit(page_size).all()

    items = []
    for r in rewards:
        items.append(
            {
                "id": r.id,
                "agent_id": r.agent_id,
                "agent_email": r.agent.email,
                "campaign_id": r.campaign_id,
                "campaign_title": r.campaign.title,
                "amount": r.amount,
                "status": r.status,
                "transaction_reference": r.transaction_reference,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
            }
        )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/stats/{agent_id}/manual-reward", response_model=AgentRewardOut)
def manual_reward_agent(
    agent_id: int,
    payload: ManualRewardRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    agent = db.query(User).filter(User.id == agent_id, User.role == UserRole.RESELLER).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found or is not a reseller")

    if payload.campaign_id:
        campaign = db.query(RewardCampaign).filter(RewardCampaign.id == payload.campaign_id).first()
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        reward_amount = payload.amount if payload.amount is not None else campaign.reward_amount
    else:
        if payload.amount is None or payload.amount <= 0:
            raise HTTPException(status_code=400, detail="Amount must be specified if campaign_id is not provided")
        reward_amount = payload.amount
        # Get or create default manual campaign
        campaign = db.query(RewardCampaign).filter(RewardCampaign.title == "Manual Admin Reward").first()
        if not campaign:
            campaign = RewardCampaign(
                title="Manual Admin Reward",
                campaign_type=CampaignType.VOLUME,
                target_metric="manual",
                target_value=Decimal("0.00"),
                reward_amount=Decimal("0.00"),
                is_active=False,
            )
            db.add(campaign)
            db.flush()

    wallet = get_or_create_wallet(db, agent_id)
    tx_ref = f"MANUAL_REWARD_{campaign.id}_{agent_id}_{int(datetime.utcnow().timestamp())}"

    # credit wallet
    credit_wallet(db, wallet, reward_amount, tx_ref, f"Manual Reward ({campaign.title}): {payload.reason}")

    # Log wallet transaction
    tx = Transaction(
        user_id=agent_id,
        reference=tx_ref,
        amount=reward_amount,
        status=TransactionStatus.SUCCESS,
        tx_type=TransactionType.WALLET_FUND,
        provider="Admin",
        failure_reason=f"Manual Reward: {payload.reason}",
    )
    db.add(tx)

    # Create AgentReward record
    reward_log = AgentReward(
        agent_id=agent_id,
        campaign_id=campaign.id,
        amount=reward_amount,
        status=AgentRewardStatus.CREDITED,
        transaction_reference=tx_ref,
    )
    db.add(reward_log)

    # Log admin audit log
    db.add(
        AdminAuditLog(
            admin_email=admin.email,
            action="agent_manual_reward",
            target=str(agent_id),
            details={
                "campaign_id": campaign.id,
                "amount": str(reward_amount),
                "reason": payload.reason,
                "tx_ref": tx_ref,
            },
        )
    )

    db.commit()
    db.refresh(reward_log)

    return {
        "id": reward_log.id,
        "agent_id": reward_log.agent_id,
        "agent_email": agent.email,
        "campaign_id": reward_log.campaign_id,
        "campaign_title": campaign.title,
        "amount": reward_log.amount,
        "status": reward_log.status,
        "transaction_reference": reward_log.transaction_reference,
        "created_at": reward_log.created_at,
        "updated_at": reward_log.updated_at,
    }
