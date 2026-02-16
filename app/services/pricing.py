from decimal import Decimal
from sqlalchemy.orm import Session
from app.models import PricingRule, PricingRole, DataPlan, UserRole


SERVICE_KEY_PREFIX = "svc"


def pricing_role_for_user(user_role: UserRole) -> PricingRole:
    # Only explicit resellers get reseller pricing.
    # Admins should see the same pricing as normal users (simplifies operations).
    return PricingRole.RESELLER if user_role == UserRole.RESELLER else PricingRole.USER


def build_service_pricing_key(tx_type: str, provider: str) -> str:
    t = str(tx_type or "").strip().lower()
    p = str(provider or "").strip().lower()
    return f"{SERVICE_KEY_PREFIX}:{t}:{p}"


def parse_pricing_key(network: str) -> dict:
    raw = str(network or "").strip().lower()
    parts = raw.split(":")
    if len(parts) == 3 and parts[0] == SERVICE_KEY_PREFIX:
        return {"kind": "service", "tx_type": parts[1], "provider": parts[2], "network": None}
    return {"kind": "data", "tx_type": "data", "provider": None, "network": raw}


def get_margin_for_key(db: Session, key: str, role: PricingRole) -> Decimal:
    rule = db.query(PricingRule).filter(
        PricingRule.network == str(key or "").strip().lower(),
        PricingRule.role == role,
    ).first()
    return Decimal(rule.margin) if rule else Decimal("0")


def get_price_for_user(db: Session, plan: DataPlan, role: UserRole) -> Decimal:
    pricing_role = pricing_role_for_user(role)
    margin = get_margin_for_key(db, plan.network, pricing_role)
    return Decimal(plan.base_price) + Decimal(margin)


def get_service_charge_for_user(
    db: Session,
    *,
    tx_type: str,
    provider: str,
    base_amount: Decimal,
    user_role: UserRole,
) -> tuple[Decimal, Decimal]:
    pricing_role = pricing_role_for_user(user_role)
    key = build_service_pricing_key(tx_type, provider)
    margin = get_margin_for_key(db, key, pricing_role)
    charge_amount = Decimal(base_amount) + Decimal(margin)
    return charge_amount, Decimal(margin)
