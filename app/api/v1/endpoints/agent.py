from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List

from app.core.database import get_db
from app.dependencies import get_current_user
from app.models import User
from app.schemas.agent import AgentDashboardStatsOut, RewardCampaignOut
from app.services.agent import get_agent_dashboard_stats, get_active_campaigns

router = APIRouter()


@router.get('/dashboard', response_model=AgentDashboardStatsOut)
def get_agent_dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Returns the core agent dashboard statistics including wallet balance,
    data sold, airtime sold, and performance summary.
    """
    return get_agent_dashboard_stats(db, user)


@router.get('/campaigns', response_model=List[RewardCampaignOut])
def get_agent_campaigns(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Returns active reward campaigns and the user's progress toward them.
    """
    return get_active_campaigns(db, user)

from app.schemas.agent import ClaimRewardIn, ClaimRewardOut, AgentReferralOut
from app.services.agent import claim_campaign_reward, get_agent_referrals

@router.get('/referrals', response_model=List[AgentReferralOut])
def get_referrals(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Returns the user's referrals and their statuses.
    """
    return get_agent_referrals(db, user)

@router.post('/claim-reward', response_model=ClaimRewardOut)
def claim_reward(payload: ClaimRewardIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Idempotent endpoint to claim a reward for a specific campaign once qualified.
    """
    return claim_campaign_reward(db, user, payload.campaign_id)
