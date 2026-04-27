from sqlalchemy import Column, Integer, String, Numeric, Boolean, Index
from app.core.database import Base
from app.models.base import TimestampMixin


class DataPlan(Base, TimestampMixin):
    __tablename__ = "data_plans"

    id = Column(Integer, primary_key=True, index=True)
    network = Column(String(32), nullable=False, index=True)
    plan_code = Column(String(64), nullable=False, unique=True)
    plan_name = Column(String(128), nullable=False)
    data_size = Column(String(32), nullable=False)
    validity = Column(String(32), nullable=False)
    base_price = Column(Numeric(12, 2), nullable=False)
    # Admin-set override: when set, this price is used instead of base_price + margin.
    display_price = Column(Numeric(12, 2), nullable=True, default=None)
    is_active = Column(Boolean, default=True, nullable=False)


Index("ix_data_plans_network_active", DataPlan.network, DataPlan.is_active)
