import logging
import re
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
        "enable_welcome_data_bonus": True,              # Marketing Gift active for Kulloma Data
        "welcome_data_bonus_amount": Decimal("300.0"),  # ₦300 gift on 2GB milestone
        "welcome_data_bonus_min_gb": Decimal("2.0"),    # 2.0 GB cumulative threshold
    },
    "meledata": {
        "ambassador_deposit_percent": Decimal("10.0"),  # 10% of 1st deposit
        "agent_data_commission_percent": Decimal("2.0"),# 2% on data purchases (configurable by admin)
        "enable_agent_data_commission": True,
        "enable_welcome_data_bonus": False,             # Off by default, admin can toggle
        "welcome_data_bonus_amount": Decimal("300.0"),
        "welcome_data_bonus_min_gb": Decimal("2.0"),
    },
    "mzdata": {
        "ambassador_deposit_percent": Decimal("5.0"),
        "agent_data_commission_percent": Decimal("2.0"),
        "enable_agent_data_commission": True,
        "enable_welcome_data_bonus": False,
        "welcome_data_bonus_amount": Decimal("300.0"),
        "welcome_data_bonus_min_gb": Decimal("2.0"),
    },
    "default": {
        "ambassador_deposit_percent": Decimal("5.0"),
        "agent_data_commission_percent": Decimal("2.0"),
        "enable_agent_data_commission": True,
        "enable_welcome_data_bonus": False,
        "welcome_data_bonus_amount": Decimal("300.0"),
        "welcome_data_bonus_min_gb": Decimal("2.0"),
    },
}


def get_brand_commission_config(
    db: Session,
    request: Optional[Request] = None,
    brand: Optional[str] = None,
    admin: Optional[User] = None,
) -> dict:
    """
    Retrieves the active commission and cashback configuration for the tenant.
    Checks DB (system_settings) overrides first; falls back to brand-specific defaults.
    """
    effective_brand = (brand or "").strip().lower()
    if not effective_brand:
        try:
            effective_brand = get_tenant_brand(request).strip().lower()
        except Exception:
            effective_brand = ""

    if not effective_brand or effective_brand == "default":
        # 1. Check logged in admin identity if available
        if admin:
            admin_ident = f"{getattr(admin, 'email', '')} {getattr(admin, 'full_name', '')}".lower()
            if "mele" in admin_ident or "mmtech" in admin_ident:
                effective_brand = "meledata"
            elif "mz" in admin_ident:
                effective_brand = "mzdata"
            elif "kulloma" in admin_ident:
                effective_brand = "kullomadata"

        # 2. Check SystemSetting for explicit brand override
        if not effective_brand or effective_brand == "default":
            brand_setting = db.query(SystemSetting).filter(SystemSetting.key.in_(["brand_name", "tenant_brand", "app_brand"])).first()
            if brand_setting and brand_setting.value:
                val = str(brand_setting.value).strip().lower()
                if "mele" in val:
                    effective_brand = "meledata"
                elif "mz" in val:
                    effective_brand = "mzdata"
                elif "kulloma" in val:
                    effective_brand = "kullomadata"

        # 3. Default to meledata as the primary platform brand
        if not effective_brand or effective_brand == "default":
            effective_brand = "meledata"

    defaults = BRAND_COMMISSION_DEFAULTS.get(effective_brand) or BRAND_COMMISSION_DEFAULTS["meledata"]

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
            val = Decimal(str(agent_setting.value.strip()))
            if val > Decimal("0"):
                agent_pct = val
        except Exception:
            pass

    # 3. Agent Data Commission Toggle
    enable_agent = defaults["enable_agent_data_commission"]
    enable_setting = db.query(SystemSetting).filter(SystemSetting.key == "comm_enable_agent_data").first()
    if enable_setting and enable_setting.value is not None:
        val = str(enable_setting.value).strip().lower()
        enable_agent = val in ("true", "1", "yes", "on")
    elif agent_pct > Decimal("0"):
        enable_agent = True

    # 4. Welcome 2GB Marketing Gift Campaign
    enable_welcome = defaults.get("enable_welcome_data_bonus", False)
    enable_welcome_setting = db.query(SystemSetting).filter(SystemSetting.key == "comm_enable_welcome_bonus").first()
    if enable_welcome_setting and enable_welcome_setting.value is not None:
        val = str(enable_welcome_setting.value).strip().lower()
        enable_welcome = val in ("true", "1", "yes", "on")

    welcome_amount = defaults.get("welcome_data_bonus_amount", Decimal("300.0"))
    welcome_amount_setting = db.query(SystemSetting).filter(SystemSetting.key == "comm_welcome_bonus_amount").first()
    if welcome_amount_setting and welcome_amount_setting.value:
        try:
            welcome_amount = Decimal(str(welcome_amount_setting.value.strip()))
        except Exception:
            pass

    welcome_min_gb = defaults.get("welcome_data_bonus_min_gb", Decimal("2.0"))
    welcome_min_gb_setting = db.query(SystemSetting).filter(SystemSetting.key == "comm_welcome_bonus_min_gb").first()
    if welcome_min_gb_setting and welcome_min_gb_setting.value:
        try:
            welcome_min_gb = Decimal(str(welcome_min_gb_setting.value.strip()))
        except Exception:
            pass

    return {
        "brand": effective_brand,
        "ambassador_deposit_percent": float(amb_pct),
        "ambassador_deposit_rate": amb_pct / Decimal("100"),
        "agent_data_commission_percent": float(agent_pct),
        "agent_data_commission_rate": agent_pct / Decimal("100"),
        "enable_agent_data_commission": enable_agent,
        "enable_welcome_data_bonus": enable_welcome,
        "welcome_data_bonus_amount": float(welcome_amount),
        "welcome_data_bonus_min_gb": float(welcome_min_gb),
    }


def update_brand_commission_config(
    db: Session,
    *,
    ambassador_deposit_percent: Optional[float] = None,
    agent_data_commission_percent: Optional[float] = None,
    enable_agent_data_commission: Optional[bool] = None,
    enable_welcome_data_bonus: Optional[bool] = None,
    welcome_data_bonus_amount: Optional[float] = None,
    welcome_data_bonus_min_gb: Optional[float] = None,
    request: Optional[Request] = None,
    admin: Optional[User] = None,
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

    if enable_welcome_data_bonus is not None:
        wb_setting = db.query(SystemSetting).filter(SystemSetting.key == "comm_enable_welcome_bonus").first()
        val = "true" if enable_welcome_data_bonus else "false"
        if not wb_setting:
            wb_setting = SystemSetting(key="comm_enable_welcome_bonus", value=val)
            db.add(wb_setting)
        else:
            wb_setting.value = val

    if welcome_data_bonus_amount is not None:
        amt_setting = db.query(SystemSetting).filter(SystemSetting.key == "comm_welcome_bonus_amount").first()
        if not amt_setting:
            amt_setting = SystemSetting(key="comm_welcome_bonus_amount", value=str(welcome_data_bonus_amount))
            db.add(amt_setting)
        else:
            amt_setting.value = str(welcome_data_bonus_amount)

    if welcome_data_bonus_min_gb is not None:
        min_setting = db.query(SystemSetting).filter(SystemSetting.key == "comm_welcome_bonus_min_gb").first()
        if not min_setting:
            min_setting = SystemSetting(key="comm_welcome_bonus_min_gb", value=str(welcome_data_bonus_min_gb))
            db.add(min_setting)
        else:
            min_setting.value = str(welcome_data_bonus_min_gb)

    db.commit()
    return get_brand_commission_config(db, request=request, admin=admin)


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
    limit: int = 300,
    request: Optional[Request] = None,
    admin: Optional[User] = None,
) -> list[dict]:
    """
    Finds recent successful data transactions made by agents that missed commission
    (e.g. before this feature was deployed), and credits them retroactively.
    """
    cfg = get_brand_commission_config(db, request=request, admin=admin)
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


def parse_size_gb(size_str: Optional[str]) -> float:
    """
    Parses a plan size string like '2GB', '1.5 GB', '500MB', '1000 MB' into float GB.
    """
    if not size_str:
        return 0.0
    try:
        s = str(size_str).strip().upper()
        match = re.search(r"(\d+(?:\.\d+)?)\s*(GB|MB)", s)
        if not match:
            return 0.0
        val = float(match.group(1))
        unit = match.group(2)
        return val if unit == "GB" else val / 1024.0
    except Exception:
        return 0.0


def has_user_funded_wallet(db: Session, user_id: int) -> bool:
    """
    Checks if a user has funded their wallet (made a deposit) at least once.
    Excludes automated cashbacks, bonus rewards, and transaction refunds.
    """
    # 1. Check successful WALLET_FUND transactions
    fund_txs = (
        db.query(Transaction)
        .filter(
            Transaction.user_id == user_id,
            or_(
                Transaction.tx_type == TransactionType.WALLET_FUND,
                Transaction.tx_type == "WALLET_FUND",
                Transaction.tx_type == "wallet_fund",
            ),
            or_(
                Transaction.status == TransactionStatus.SUCCESS,
                Transaction.status == "SUCCESS",
                Transaction.status == "success",
            ),
            Transaction.amount > 0,
        )
        .all()
    )
    for tx in fund_txs:
        ref = (tx.reference or "").upper()
        if not (ref.startswith("COMM_") or ref.startswith("WELCOME_") or ref.startswith("BONUS_") or ref.startswith("REFUND_")):
            return True

    # 2. Check WalletLedger credit entries (deposits, transfers, admin credits)
    from app.models.wallet import Wallet
    wallet = db.query(Wallet).filter(Wallet.user_id == user_id).first()
    if wallet:
        credits = (
            db.query(WalletLedger)
            .filter(
                WalletLedger.wallet_id == wallet.id,
                or_(
                    WalletLedger.entry_type == LedgerType.CREDIT,
                    WalletLedger.entry_type == "CREDIT",
                    WalletLedger.entry_type == "credit",
                ),
                WalletLedger.amount > 0,
            )
            .all()
        )
        for entry in credits:
            ref = (entry.reference or "").upper()
            desc = (entry.description or "").lower()
            if ref.startswith("COMM_") or ref.startswith("WELCOME_") or ref.startswith("BONUS_") or ref.startswith("REFUND_"):
                continue
            if any(term in desc for term in ["fund", "deposit", "paystack", "monnify", "korapay", "transfer", "bank", "credit", "topup"]):
                return True

    # 3. If user has paid for data purchases, they undeniably deposited/funded money to make those orders
    has_paid_data = (
        db.query(Transaction)
        .filter(
            Transaction.user_id == user_id,
            or_(
                Transaction.tx_type == TransactionType.DATA,
                Transaction.tx_type == "DATA",
                Transaction.tx_type == "data",
            ),
            or_(
                Transaction.status == TransactionStatus.SUCCESS,
                Transaction.status == "SUCCESS",
                Transaction.status == "success",
            ),
            Transaction.amount > 0,
        )
        .first()
    )
    if has_paid_data:
        return True

    return False


def get_user_total_data_gb(db: Session, user_id: int) -> float:
    """
    Calculates cumulative data volume in GB across all successful data purchases.
    Supports single or multiple purchases (e.g. 1GB + 1GB = 2GB).
    Combines AgentStat and individual transaction records for 100% accuracy.
    """
    from app.models.agent import AgentStat
    from app.models.data_plan import DataPlan

    # 1. Check AgentStat
    stat = db.query(AgentStat).filter(AgentStat.agent_id == user_id).first()
    stat_gb = (float(stat.total_data_mb) / 1024.0) if stat and stat.total_data_mb else 0.0

    # 2. Sum up from successful DATA transactions
    data_txs = (
        db.query(Transaction)
        .filter(
            Transaction.user_id == user_id,
            or_(
                Transaction.tx_type == TransactionType.DATA,
                Transaction.tx_type == "DATA",
                Transaction.tx_type == "data",
            ),
            or_(
                Transaction.status == TransactionStatus.SUCCESS,
                Transaction.status == "SUCCESS",
                Transaction.status == "success",
            ),
            Transaction.amount > 0,
        )
        .all()
    )

    tx_sum_gb = 0.0
    for tx in data_txs:
        gb = 0.0
        # Check text fields for size pattern (e.g. "2GB", "1GB", "500MB")
        for val in [tx.data_plan_code, tx.external_reference, getattr(tx, "provider_plan_id", None)]:
            if val:
                parsed = parse_size_gb(val)
                if parsed > 0:
                    gb = parsed
                    break

        # If not parsed, lookup DataPlan
        if gb <= 0.0 and tx.data_plan_code:
            dp = db.query(DataPlan).filter(DataPlan.plan_code == tx.data_plan_code).first()
            if dp and dp.data_size:
                gb = parse_size_gb(dp.data_size)

        # Fallback to amount-based approximation (~₦260 per GB)
        if gb <= 0.0:
            try:
                amt = float(tx.amount)
                if amt > 0:
                    gb = amt / 260.0
            except Exception:
                gb = 0.0

        tx_sum_gb += gb

    return max(stat_gb, tx_sum_gb)


def check_and_award_welcome_data_bonus(
    db: Session,
    *,
    user: User,
    request: Optional[Request] = None,
    admin: Optional[User] = None,
    brand: Optional[str] = None,
) -> Optional[Decimal]:
    """
    Kulloma Data Marketing Campaign:
    When a user signs up, deposits/funds their wallet, and buys data totaling 2GB or above
    (either in a single purchase or cumulatively, e.g. 1GB + 1GB), their wallet is
    gifted ₦300 automatically.

    Guards:
    1. Tenant campaign must be enabled (enable_welcome_data_bonus).
    2. User must have deposited / funded their wallet at least once.
    3. User cumulative data volume >= welcome_data_bonus_min_gb (default 2.0 GB).
    4. Idempotency: award exactly once per user using reference WELCOME_2GB_{user.id}.
    """
    if not user or not getattr(user, "id", None):
        return None

    # Resolve target brand: explicit brand > request tenant > global tenant > user.brand > email > default
    target_brand = (brand or "").strip().lower()
    if not target_brand and request is not None:
        target_brand = get_tenant_brand(request).strip().lower()
    if not target_brand:
        try:
            t_brand = get_tenant_brand(None).strip().lower()
            if t_brand and t_brand != "default":
                target_brand = t_brand
        except Exception:
            pass
    if not target_brand or target_brand == "default":
        u_brand = (getattr(user, "brand", "") or "").strip().lower()
        if u_brand and u_brand != "default":
            target_brand = u_brand
    if not target_brand or target_brand == "default":
        if getattr(user, "email", None) and "kulloma" in str(user.email).lower():
            target_brand = "kullomadata"
    if not target_brand or target_brand == "default":
        target_brand = "kullomadata"

    cfg = get_brand_commission_config(db, request=request, brand=target_brand, admin=admin)
    if not cfg.get("enable_welcome_data_bonus"):
        return None

    bonus_amount = Decimal(str(cfg.get("welcome_data_bonus_amount") or 300.0)).quantize(Decimal("0.01"))
    min_gb = float(cfg.get("welcome_data_bonus_min_gb") or 2.0)
    if bonus_amount <= Decimal("0"):
        return None

    bonus_ref = f"WELCOME_2GB_{user.id}"

    # 1. Idempotency Check: User can only receive the welcome bonus once
    existing_tx = db.query(Transaction).filter(Transaction.reference == bonus_ref).first()
    if existing_tx:
        return None
    existing_ledger = db.query(WalletLedger).filter(WalletLedger.reference == bonus_ref).first()
    if existing_ledger:
        return None

    # 2. Check if user has funded their wallet (deposit requirement)
    if not has_user_funded_wallet(db, user.id):
        logger.debug("User %s (%d) has not funded wallet yet; not eligible for welcome data bonus.", user.email, user.id)
        return None

    # 3. Check cumulative data volume (>= min_gb)
    total_gb = get_user_total_data_gb(db, user.id)
    if total_gb < (min_gb - 0.001):
        logger.debug("User %s (%d) has purchased %.2f GB, below threshold of %.2f GB.", user.email, user.id, total_gb, min_gb)
        return None

    # 4. Award Welcome Gift: Credit User Wallet
    from app.services.wallet import get_or_create_wallet, credit_wallet
    wallet = get_or_create_wallet(db, user.id, commit=False)
    desc = f"Welcome Gift: ₦{bonus_amount:,.0f} Data Activation Bonus ({total_gb:.1f}GB Milestone)"
    credit_wallet(db, wallet, bonus_amount, bonus_ref, desc, commit=False)

    # 5. Create Transaction Record
    tx = Transaction(
        user_id=user.id,
        reference=bonus_ref,
        amount=bonus_amount,
        status=TransactionStatus.SUCCESS,
        tx_type=TransactionType.WALLET_FUND,
        network="KULLOMA_BONUS",
        data_plan_code=f"WELCOME_{min_gb:.0f}GB",
        external_reference=f"CUMULATIVE_{total_gb:.2f}GB",
    )
    db.add(tx)
    db.commit()

    logger.info(
        "Awarded ₦%s welcome data bonus to user %d (%s). Cumulative data: %.2f GB.",
        bonus_amount, user.id, user.email, total_gb
    )

    # 6. Push Notification
    if getattr(user, "fcm_token", None):
        try:
            from app.services.push_notification import PushNotificationService
            PushNotificationService.send_to_token(
                token=user.fcm_token,
                title="🎁 ₦300 Welcome Gift Credited!",
                body=f"Congratulations! You've unlocked your ₦{bonus_amount:,.0f} welcome gift for buying {total_gb:.1f}GB of data. Your wallet has been credited!",
                sound_type="balance_success",
                data={"type": "welcome_bonus", "reference": bonus_ref, "amount": str(bonus_amount)},
            )
        except Exception as fcm_err:
            logger.warning("Failed to send welcome bonus push notification to %s: %s", user.email, fcm_err)

    return bonus_amount


def reconcile_missing_welcome_bonuses(
    db: Session,
    request: Optional[Request] = None,
    admin: Optional[User] = None,
    brand: Optional[str] = None,
    limit: int = 1000,
) -> list[dict]:
    """
    Scans users who have funded their wallet and purchased >= 2GB of data
    but haven't yet received their ₦300 welcome gift, and awards it retroactively.
    """
    target_brand = (brand or "").strip().lower()
    if not target_brand and request is not None:
        target_brand = get_tenant_brand(request).strip().lower()
    if not target_brand and admin and getattr(admin, "email", None) and "kulloma" in str(admin.email).lower():
        target_brand = "kullomadata"
    if not target_brand or target_brand == "default":
        target_brand = "kullomadata"

    cfg = get_brand_commission_config(db, request=request, admin=admin, brand=target_brand)
    if not cfg.get("enable_welcome_data_bonus"):
        logger.info("Welcome data bonus is not enabled for brand '%s', skipping reconcile.", cfg.get("brand"))
        return []

    # Find users who have successful data transactions
    users_with_data = (
        db.query(User)
        .join(Transaction, Transaction.user_id == User.id)
        .filter(
            or_(
                Transaction.tx_type == TransactionType.DATA,
                Transaction.tx_type == "DATA",
                Transaction.tx_type == "data",
            ),
            or_(
                Transaction.status == TransactionStatus.SUCCESS,
                Transaction.status == "SUCCESS",
                Transaction.status == "success",
            ),
            Transaction.amount > 0,
        )
        .distinct()
        .order_by(User.id.desc())
        .limit(limit)
        .all()
    )

    logger.info("Evaluating %d users for missing welcome data bonuses on %s...", len(users_with_data), target_brand)
    awarded = []
    for u in users_with_data:
        bonus_ref = f"WELCOME_2GB_{u.id}"
        already_has = db.query(Transaction).filter(Transaction.reference == bonus_ref).first()
        if already_has:
            continue
        bonus = check_and_award_welcome_data_bonus(db, user=u, request=request, admin=admin, brand=target_brand)
        if bonus:
            awarded.append({
                "user_id": u.id,
                "email": u.email,
                "amount": float(bonus),
                "reference": bonus_ref,
            })

    logger.info("Reconciliation complete. Awarded %d missing welcome data bonuses.", len(awarded))
    return awarded
