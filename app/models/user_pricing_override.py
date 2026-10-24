from sqlalchemy import Column, Integer, Numeric, ForeignKey, Index
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.models.base import TimestampMixin


class UserPricingOverride(Base, TimestampMixin):
    __tablename__ = "user_pricing_overrides"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    data_plan_id = Column(Integer, ForeignKey("data_plans.id", ondelete="CASCADE"), nullable=False)
    custom_price = Column(Numeric(12, 2), nullable=False)

    user = relationship("User", backref="pricing_overrides")
    data_plan = relationship("DataPlan")


Index("ix_user_pricing_overrides_user_plan", UserPricingOverride.user_id, UserPricingOverride.data_plan_id, unique=True)
