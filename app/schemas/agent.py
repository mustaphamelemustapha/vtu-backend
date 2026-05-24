from pydantic import BaseModel, ConfigDict
from typing import List, Optional
from datetime import datetime
from decimal import Decimal
from app.models.agent import CampaignType, AgentRewardStatus

class AgentDashboardStatsOut(BaseModel):
    wallet_balance: Decimal
    today_data_gb: float
    today_airtime: Decimal
    month_data_gb: float
    month_airtime: Decimal
    total_transactions: int
    agent_status: str
    performance_summary: str

class RewardCampaignOut(BaseModel):
    id: int
    title: str
    campaign_type: str
    target_metric: str
    target_value: float
    reward_amount: Decimal
    is_active: bool
    progress_value: float
    is_qualified: bool

    model_config = ConfigDict(from_attributes=True)

class AgentReferralOut(BaseModel):
    id: int
    referred_user_name: str
    status: str
    qualified_at: Optional[datetime]
    rewarded_at: Optional[datetime]
    created_at: datetime
    
class ClaimRewardIn(BaseModel):
    campaign_id: int

class ClaimRewardOut(BaseModel):
    success: bool
    message: str
    amount_credited: Decimal
