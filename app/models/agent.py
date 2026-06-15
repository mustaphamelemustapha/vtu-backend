import enum
from sqlalchemy import Column, Integer, String, Boolean, Enum, ForeignKey, Numeric, DateTime, Index
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.models.base import TimestampMixin


class CampaignType(str, enum.Enum):
    REFERRAL = "referral"
    VOLUME = "volume"


class RewardCampaign(Base, TimestampMixin):
    __tablename__ = "reward_campaigns"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    campaign_type = Column(Enum(CampaignType), nullable=False, default=CampaignType.REFERRAL)
    target_metric = Column(String(64), nullable=False) # e.g., "data_mb", "airtime_naira"
    target_value = Column(Numeric(12, 2), nullable=False) # e.g., 51200 for 50GB
    reward_amount = Column(Numeric(12, 2), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    is_agent_only = Column(Boolean, default=True, nullable=False)
    
    agent_rewards = relationship("AgentReward", back_populates="campaign")


class AgentRewardStatus(str, enum.Enum):
    PENDING = "pending"
    CREDITED = "credited"
    FAILED = "failed"


class AgentReward(Base, TimestampMixin):
    __tablename__ = "agent_rewards"

    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    campaign_id = Column(Integer, ForeignKey("reward_campaigns.id"), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    status = Column(Enum(AgentRewardStatus), nullable=False, default=AgentRewardStatus.PENDING)
    transaction_reference = Column(String(64), nullable=True, index=True)

    agent = relationship("User", foreign_keys=[agent_id])
    campaign = relationship("RewardCampaign", back_populates="agent_rewards")


class AgentStat(Base, TimestampMixin):
    __tablename__ = "agent_stats"

    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False, index=True)
    
    # All-time stats
    total_data_mb = Column(Integer, default=0, nullable=False)
    total_airtime_amount = Column(Numeric(12, 2), default=0, nullable=False)
    total_transactions = Column(Integer, default=0, nullable=False)

    agent = relationship("User", foreign_keys=[agent_id])
