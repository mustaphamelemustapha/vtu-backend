import logging
from decimal import Decimal
from typing import Optional
from starlette.requests import Request
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.core.tenant import get_tenant_brand
from app.models.user import User, UserRole
from app.models.wallet_ledger import WalletLedger, LedgerType
from app.models.transaction import Transaction, TransactionStatus, TransactionType
from app.models.system_setting import SystemSetting

logger = logging.getLogger(__name__)

# Default commission profiles per tenant brand
BRAND_COMMISSION_DEFAULTS = {
    "kullomadata": {
        "ambassador_deposit_percent": Decimal("5.0"),   # 5% of 1st deposit
        "agent_data_commission_percent": Decimal("2.0"),# 2% on data purchases
        "enable_agent_data_commission": True,
    },
    "meledata": {
        "ambassador_deposit_percent": Decimal("10.0"),  # 10% of 1st deposit
        "agent_data_commission_percent": Decimal("0.0"),# Uses fixed plan agent_price
        "enable_agent_data_commission": False,
    },
    "mzdata": {
        "ambassador_deposit_percent": Decimal("5.0"),
        "agent_data_commission_percent": Decimal("0.0"),
        "enable_agent_data_commission": False,
    },
    "default": {
        "ambassador_deposit_percent": Decimal("5.0"),
        "agent_data_commission_percent": Decimal("0.0"),
        "enable_agent_data_commission": False,
    },
}


def get_brand_commission_config(db: Session, request: Optional[Request] = None, brand: Optional[str] = None) -> dict:
    """
    Retrieves the active commission and cashback configuration for the tenant.
    Checks DB (system_settings) overrides first; falls back to brand-specific defaults.
    """
    effective_brand = (brand or get_tenant_brand(request)).strip().lower()
    if effective_brand == "default":
        # Check if database records indicate the tenant brand
        try:
            if db.query(User).filter(User.email.ilike("%kulloma%")).first():
                effective_brand = "kullomadata"
            elif db.query(User).filter(User.email.ilike("%mzdata%")).first():
                effective_brand = "mzdata"
            elif db.query(User).filter(User.email.ilike("%meledata%")).first():
                effective_brand = "meledata"
        except Exception:
            pass

    defaults = BRAND_COMMISSION_DEFAULTS.get(effective_brand) or BRAND_COMMISSION_DEFAULTS["default"]

    # 1. Ambassador Deposit Commission %
    amb_pct = defaults["ambassador_deposit_percent"]
    amb_setting = db.query(SystemSetting).filter(SystemSetting.key == "comm_ambassador_deposit_percent").first()
    if amb_setting and amb_setting.value:
        try:
            amb_pct = Decimal(str(amb_setting.value.strip()))
        except Exception:
            pass

    # 2. Agent Data Purchase Commission %
    agent_pct = defaults["agent_data_commission_percent"]
    agent_setting = db.query(SystemSetting).filter(SystemSetting.key == "comm_agent_data_percent").first()
    if agent_setting and agent_setting.value:
        try:
            agent_pct = Decimal(str(agent_setting.value.strip()))
        except Exception:
            pass

    # 3. Agent Data Commission Toggle
    enable_agent = defaults["enable_agent_data_commission"]
    enable_setting = db.query(SystemSetting).filter(SystemSetting.key == "comm_enable_agent_data").first()
    if enable_setting and enable_setting.value is not None:
        val = str(enable_setting.value).strip().lower()
        enable_agent = val in ("true", "1", "yes", "on")

    return {
        "brand": effective_brand,
        "ambassador_deposit_percent": float(amb_pct),
        "ambassador_deposit_rate": amb_pct / Decimal("100"),
        "agent_data_commission_percent": float(agent_pct),
        "agent_data_commission_rate": agent_pct / Decimal("100"),
        "enable_agent_data_commission": enable_agent,
    }


def update_brand_commission_config(
    db: Session,
    *,
    ambassador_deposit_percent: Optional[float] = None,
    agent_data_commission_percent: Optional[float] = None,
    enable_agent_data_commission: Optional[bool] = None,
) -> dict:
    """
    Updates or inserts commission settings into system_settings.
    """
    if ambassador_deposit_percent is not None:
        amb_setting = db.query(SystemSetting).filter(SystemSetting.key == "comm_ambassador_deposit_percent").first()
        if not amb_setting:
            amb_setting = SystemSetting(key="comm_ambassador_deposit_percent", value=str(ambassador_deposit_percent))
            db.add(amb_setting)
        else:
            amb_setting.value = str(ambassador_deposit_percent)

    if agent_data_commission_percent is not None:
        agent_setting = db.query(SystemSetting).filter(SystemSetting.key == "comm_agent_data_percent").first()
        if not agent_setting:
            agent_setting = SystemSetting(key="comm_agent_data_percent", value=str(agent_data_commission_percent))
            db.add(agent_setting)
        else:
            agent_setting.value = str(agent_data_commission_percent)

    if enable_agent_data_commission is not None:
        enable_setting = db.query(SystemSetting).filter(SystemSetting.key == "comm_enable_agent_data").first()
        val = "true" if enable_agent_data_commission else "false"
        if not enable_setting:
            enable_setting = SystemSetting(key="comm_enable_agent_data", value=val)
            db.add(enable_setting)
        else:
            enable_setting.value = val

    db.commit()
    return get_brand_commission_config(db)


def record_agent_data_commission(
    db: Session,
    *,
    user: User,
    amount: Decimal,
    reference: str,
    network: str,
    plan_name: str,
    request: Optional[Request] = None,
) -> Optional[Decimal]:
    """
    Calculates and credits the agent's commission on each successful data transaction.
    For Kulloma Data agents, this grants the 2% cashback/commission on data purchase.
    """
    # 1. Role Guard: Only Agents qualify
    user_role = getattr(user, "role", None)
    role_str = (user_role.value if hasattr(user_role, "value") else str(user_role or "")).strip().lower()
    if role_str not in ("agent", "reseller") and not role_str.endswith(".agent"):
        return None

    # 2. Config Guard
    cfg = get_brand_commission_config(db, request=request)
    if not cfg["enable_agent_data_commission"] or cfg["agent_data_commission_rate"] <= Decimal("0"):
        return None

    # 3. Calculate Commission
    base_amount = Decimal(str(amount or 0))
    if base_amount <= Decimal("0"):
        return None

    comm_amount = (base_amount * cfg["agent_data_commission_rate"]).quantize(Decimal("0.01"))
    if comm_amount <= Decimal("0"):
        return None

    comm_ref = f"COMM_{reference}"

    # 4. Idempotency Check: Prevent duplicate crediting
    existing = db.query(Transaction).filter(Transaction.reference == comm_ref).first()
    if existing:
        return None
    existing_ledger = db.query(WalletLedger).filter(WalletLedger.reference == comm_ref).first()
    if existing_ledger:
        return None

    # 5. Credit Agent Wallet
    from app.services.wallet import get_or_create_wallet, credit_wallet
    wallet = get_or_create_wallet(db, user.id, commit=False)
    pct_str = f"{cfg['agent_data_commission_percent']:.0f}%" if cfg['agent_data_commission_percent'].is_integer() else f"{cfg['agent_data_commission_percent']:.1f}%"
    desc = f"Agent Commission ({pct_str} Data Cashback): {plan_name}"
    
    credit_wallet(db, wallet, comm_amount, comm_ref, desc, commit=False)

    tx = Transaction(
        user_id=user.id,
        reference=comm_ref,
        amount=comm_amount,
        status=TransactionStatus.SUCCESS,
        tx_type=TransactionType.WALLET_FUND,
        network=network,
        data_plan_code=f"AGENT_COMM_{pct_str}",
        external_reference=reference,
    )
    db.add(tx)
    db.commit()

    logger.info(
        "Agent data commission of ₦%s credited to user %d (%s) for tx %s",
        comm_amount, user.id, user.email, reference
    )

    # 6. Push Notification
    if getattr(user, "fcm_token", None):
        try:
            from app.services.push_notification import PushNotificationService
            PushNotificationService.send_to_token(
                token=user.fcm_token,
                title="Commission Earned! 💰",
                body=f"You earned ₦{comm_amount:,.2f} ({pct_str} commission) on your {plan_name} purchase.",
                sound_type="balance_success",
                data={"type": "commission", "reference": comm_ref, "amount": str(comm_amount)},
            )
        except Exception as fcm_err:
            logger.warning("Failed to send agent commission push notification: %s", fcm_err)

    return comm_amount


def reconcile_missing_agent_commissions(
    db: Session,
    limit: int = 100,
    request: Optional[Request] = None,
) -> list[dict]:
    """
    Finds recent successful data transactions made by agents that missed commission
    (e.g. before this feature was deployed), and credits them retroactively.
    """
    cfg = get_brand_commission_config(db, request=request)
    logger.info("Reconciling agent commissions with config: %s", cfg)
    if not cfg["enable_agent_data_commission"] or cfg["agent_data_commission_rate"] <= Decimal("0"):
        logger.info("Agent data commission is not enabled for brand '%s', skipping reconcile.", cfg.get("brand"))
        return []

    recent_txs = (
        db.query(Transaction)
        .join(User, Transaction.user_id == User.id)
        .filter(
            or_(
                Transaction.tx_type == TransactionType.DATA,
                Transaction.tx_type == "data",
                Transaction.tx_type == "DATA",
            ),
            or_(
                Transaction.status == TransactionStatus.SUCCESS,
                Transaction.status == "success",
                Transaction.status == "SUCCESS",
            ),
            Transaction.amount > 0,
        )
        .order_by(Transaction.id.desc())
        .limit(limit)
        .all()
    )

    logger.info("Found %d recent successful data transactions to evaluate for agent cashback.", len(recent_txs))
    credited = []
    for tx in recent_txs:
        role_str = (tx.user.role.value if hasattr(tx.user.role, "value") else str(tx.user.role or "")).strip().lower()
        if role_str not in ("agent", "reseller") and not role_str.endswith(".agent"):
            continue

        comm_ref = f"COMM_{tx.reference}"
        already_has = db.query(Transaction).filter(Transaction.reference == comm_ref).first()
        if already_has:
            continue

        comm_earned = record_agent_data_commission(
            db,
            user=tx.user,
            amount=Decimal(str(tx.amount)),
            reference=tx.reference,
            network=tx.network or "DATA",
            plan_name=tx.data_plan_code or "Data Plan",
            request=request,
        )
        if comm_earned:
            credited.append({
                "user_id": tx.user_id,
                "email": tx.user.email,
                "amount": float(comm_earned),
                "reference": comm_ref,
                "tx_reference": tx.reference,
            })
            logger.info("Reconciled ₦%s agent cashback to %s for tx %s", comm_earned, tx.user.email, tx.reference)

    logger.info("Reconciliation complete. Credited %d agent commissions.", len(credited))

    if credited:
        logger.info("Reconciled %d missing agent data commissions.", len(credited))
    return credited
