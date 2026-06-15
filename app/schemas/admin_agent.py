from pydantic import BaseModel
from decimal import Decimal
from datetime import datetime
from typing import Optional, List
from app.models.agent import CampaignType, AgentRewardStatus


class RewardCampaignCreate(BaseModel):
    title: str
    campaign_type: CampaignType
    target_metric: str
    target_value: Decimal
    reward_amount: Decimal
    is_active: bool = True
    is_agent_only: bool = True


class RewardCampaignUpdate(BaseModel):
    title: Optional[str] = None
    campaign_type: Optional[CampaignType] = None
    target_metric: Optional[str] = None
    target_value: Optional[Decimal] = None
    reward_amount: Optional[Decimal] = None
    is_active: Optional[bool] = None
    is_agent_only: Optional[bool] = None


class RewardCampaignOut(BaseModel):
    id: int
    title: str
    campaign_type: CampaignType
    target_metric: str
    target_value: Decimal
    reward_amount: Decimal
    is_active: bool
    is_agent_only: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True


class RewardCampaignsResponse(BaseModel):
    items: List[RewardCampaignOut]
    total: int


class AgentStatOut(BaseModel):
    id: int
    agent_id: int
    agent_email: str
    agent_full_name: str
    total_data_mb: int
    total_airtime_amount: Decimal
    total_transactions: int
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True


class AgentStatsResponse(BaseModel):
    items: List[AgentStatOut]
    total: int
    page: int
    page_size: int


class AgentStatOverride(BaseModel):
    total_data_mb: Optional[int] = None
    total_airtime_amount: Optional[Decimal] = None
    total_transactions: Optional[int] = None


class ManualRewardRequest(BaseModel):
    campaign_id: Optional[int] = None
    amount: Optional[Decimal] = None
    reason: str


class AgentRewardOut(BaseModel):
    id: int
    agent_id: int
    agent_email: str
    campaign_id: int
    campaign_title: str
    amount: Decimal
    status: AgentRewardStatus
    transaction_reference: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True


class AgentRewardsResponse(BaseModel):
    items: List[AgentRewardOut]
    total: int
    page: int
    page_size: int
