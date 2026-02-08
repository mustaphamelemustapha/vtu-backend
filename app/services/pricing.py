from decimal import Decimal
from sqlalchemy.orm import Session
from app.models import PricingRule, PricingRole, DataPlan, UserRole


def get_price_for_user(db: Session, plan: DataPlan, role: UserRole) -> Decimal:
    pricing_role = PricingRole.USER if role == UserRole.USER else PricingRole.RESELLER
    rule = db.query(PricingRule).filter(
        PricingRule.network == plan.network,
        PricingRule.role == pricing_role,
    ).first()
    margin = rule.margin if rule else Decimal("0")
    return Decimal(plan.base_price) + Decimal(margin)
