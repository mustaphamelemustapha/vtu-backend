import enum
from sqlalchemy import Column, Integer, ForeignKey, String, Enum, Numeric, DateTime, Index
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.models.base import TimestampMixin


class ReferralStatus(str, enum.Enum):
    PENDING = "pending"
    QUALIFIED = "qualified"
    REWARDED = "rewarded"


class Referral(Base, TimestampMixin):
    __tablename__ = "referrals"

    id = Column(Integer, primary_key=True, index=True)
    referrer_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    referred_user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False, index=True)
    referral_code_used = Column(String(32), nullable=False, index=True)
    accumulated_mb = Column(Integer, default=0, nullable=False)
    target_mb = Column(Integer, default=51200, nullable=False)
    reward_amount = Column(Numeric(12, 2), default=2000, nullable=False)
    status = Column(Enum(ReferralStatus, name="referral_status"), nullable=False, default=ReferralStatus.PENDING)
    qualifying_transaction_reference = Column(String(64), nullable=True, index=True)
    reward_transaction_reference = Column(String(64), nullable=True, index=True)
    qualified_at = Column(DateTime(timezone=True), nullable=True)
    rewarded_at = Column(DateTime(timezone=True), nullable=True)

    referrer = relationship("User", foreign_keys=[referrer_id], back_populates="referrals_sent")
    referred_user = relationship("User", foreign_keys=[referred_user_id], back_populates="referral_received")
    contributions = relationship(
        "ReferralContribution",
        back_populates="referral",
        cascade="all, delete-orphan",
    )


class ReferralContribution(Base, TimestampMixin):
    __tablename__ = "referral_contributions"

    id = Column(Integer, primary_key=True, index=True)
    referral_id = Column(Integer, ForeignKey("referrals.id"), nullable=False, index=True)
    transaction_reference = Column(String(64), unique=True, nullable=False, index=True)
    mb = Column(Integer, nullable=False)
    reversed_at = Column(DateTime(timezone=True), nullable=True)

    referral = relationship("Referral", back_populates="contributions")


Index("ix_referrals_referrer_status", Referral.referrer_id, Referral.status)
Index("ix_referral_contributions_referral_reversed", ReferralContribution.referral_id, ReferralContribution.reversed_at)
