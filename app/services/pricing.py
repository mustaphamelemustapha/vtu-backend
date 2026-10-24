from decimal import Decimal
from typing import Any
from sqlalchemy.orm import Session
from app.models import PricingRule, PricingRole, DataPlan, UserRole


SERVICE_KEY_PREFIX = "svc"


def pricing_role_for_user(user_role: UserRole) -> PricingRole:
    # Only explicit resellers get reseller pricing.
    # Admins should see the same pricing as normal users (simplifies operations).
    return PricingRole.RESELLER if user_role == UserRole.AGENT else PricingRole.USER


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


def get_margin_for_key(db: Session, key: str, role: PricingRole) -> tuple[Decimal, str]:
    """Return (margin_amount, margin_type) for a pricing key and role."""
    rule = None
    try:
        rule = db.query(PricingRule).filter(
            PricingRule.network == str(key or "").strip().lower(),
            PricingRule.role == role,
        ).first()
    except Exception:
        try:
            from sqlalchemy import cast, String
            role_val = getattr(role, "value", str(role)).lower()
            rule = db.query(PricingRule).filter(
                PricingRule.network == str(key or "").strip().lower(),
                cast(PricingRule.role, String) == role_val,
            ).first()
        except Exception:
            rule = None
    if not rule:
        return Decimal("0"), "fixed"
    margin_type = str(getattr(rule, "margin_type", None) or "fixed").strip().lower()
    if margin_type not in ("fixed", "percentage"):
        margin_type = "fixed"
    return Decimal(rule.margin), margin_type


def apply_margin(base_price: Decimal, margin: Decimal, margin_type: str) -> Decimal:
    """Calculate final price from base, margin and margin_type."""
    base = Decimal(base_price)
    m = Decimal(margin)
    if margin_type == "percentage":
        return base + (base * m / Decimal("100"))
    return base + m


def get_price_for_user(db: Session, plan: DataPlan, role: Any, is_developer: bool = False) -> Decimal:
    # 0. Check custom per-user pricing override first (takes highest precedence)
    user_id = getattr(role, "id", None)
    if user_id is None and isinstance(role, int):
        user_id = role
    if user_id:
        try:
            from app.models.user_pricing_override import UserPricingOverride
            override = db.query(UserPricingOverride).filter(
                UserPricingOverride.user_id == user_id,
                UserPricingOverride.data_plan_id == plan.id
            ).first()
            if override:
                return Decimal(str(override.custom_price))
        except Exception:
            pass

    # 1. Check if user is accessing as an API developer, or has developer flag
    if is_developer or getattr(role, "is_developer", False) or str(getattr(role, "role", role)).lower() in ("developer", "api"):
        api_price = getattr(plan, "api_price", None)
        if api_price is not None:
            try:
                return Decimal(api_price)
            except Exception:
                pass

    user_role = getattr(role, "role", role)
    pricing_role = pricing_role_for_user(user_role)
    
    if pricing_role == PricingRole.RESELLER:
        agent_price = getattr(plan, "agent_price", None)
        if agent_price is not None:
            try:
                return Decimal(agent_price)
            except Exception:
                pass

    display = getattr(plan, "display_price", None)
    if display is not None:
        try:
            return Decimal(display)
        except Exception:
            pass

    margin, margin_type = get_margin_for_key(db, plan.network, pricing_role)
    return apply_margin(Decimal(plan.base_price), margin, margin_type)


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
    margin, margin_type = get_margin_for_key(db, key, pricing_role)
    charge_amount = apply_margin(Decimal(base_amount), margin, margin_type)
    return charge_amount, Decimal(margin)
