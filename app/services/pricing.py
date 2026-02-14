from decimal import Decimal
from sqlalchemy.orm import Session
from app.models import PricingRule, PricingRole, DataPlan, UserRole


def get_price_for_user(db: Session, plan: DataPlan, role: UserRole) -> Decimal:
    # Only explicit resellers get reseller pricing.
    # Admins should see the same pricing as normal users (simplifies operations).
    pricing_role = PricingRole.RESELLER if role == UserRole.RESELLER else PricingRole.USER
    rule = db.query(PricingRule).filter(
        PricingRule.network == plan.network,
        PricingRule.role == pricing_role,
    ).first()
    margin = rule.margin if rule else Decimal("0")
    return Decimal(plan.base_price) + Decimal(margin)
