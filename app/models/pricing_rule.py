import enum
from sqlalchemy import Column, Integer, String, Numeric, Index
from app.core.database import Base, SafeSQLEnum
from app.models.base import TimestampMixin


class PricingRole(str, enum.Enum):
    USER = "user"
    RESELLER = "reseller"


class MarginType(str, enum.Enum):
    FIXED = "fixed"
    PERCENTAGE = "percentage"


class PricingRule(Base, TimestampMixin):
    __tablename__ = "pricing_rules"

    id = Column(Integer, primary_key=True, index=True)
    network = Column(String(32), nullable=False)
    role = Column(
        SafeSQLEnum(
            PricingRole,
            name="pricingrole",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    margin = Column(Numeric(12, 2), nullable=False, default=0)
    # 'fixed' adds a flat amount; 'percentage' adds margin% of base_price.
    margin_type = Column(String(16), nullable=False, server_default="fixed")


Index("ix_pricing_rules_network_role", PricingRule.network, PricingRule.role, unique=True)
