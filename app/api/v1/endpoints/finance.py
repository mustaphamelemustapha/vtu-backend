import logging
from decimal import Decimal
from typing import Any, List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, text, cast, String, inspect
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.core.database import get_db
from app.dependencies import get_current_user, require_admin
from app.models import (
    Transaction, TransactionStatus, TransactionType,
    DataPlan, AgentReward, AgentRewardStatus,
    Referral, ReferralStatus, Wallet,
    FinancialLedger, FinancialCategory, EntryType,
    User, ServiceTransaction
)

router = APIRouter()
logger = logging.getLogger(__name__)

# --- Pydantic Schemas ---

class FinanceOverviewResponse(BaseModel):
    revenue: dict # { "actual": float }
    cogs: dict # { "total": float, "data_cogs_estimated": float, "airtime_cogs_estimated": float, "other_cogs_estimated": float, "is_estimated": bool }
    gross_margin: float
    promotional_expense: float
    referral_expense: float
    agent_commission_expense: float
    payment_fees: dict # { "total": float, "is_estimated": bool }
    net_profit: float
    is_net_profit_estimated: bool
    customer_wallet_liability: float
    company_assets: dict # { "total": float, "providers": list }
    owner_capital: float
    business_debts: float
    receivables: float
    net_equity: float
    reconciliation_gap: float

class FinancialLedgerCreate(BaseModel):
    category: str
    entry_type: str
    amount: float
    party: str
    description: Optional[str] = None
    reference: Optional[str] = None

class FinancialLedgerResponse(BaseModel):
    id: int
    category: str
    entry_type: str
    amount: float
    party: Optional[str]
    description: Optional[str]
    reference: str
    status: str
    created_at: Optional[datetime] = None
    
    class Config:
        orm_mode = True
        from_attributes = True

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

@router.get("/overview", response_model=FinanceOverviewResponse)
def get_finance_overview(
    db: Session = Depends(get_db),
    user: User = Depends(require_admin)
) -> Any:
    """
    Get strict financial metrics separating Revenue, Expenses, Liabilities, and Equity.
    Accounts for service sales, provider COGS, agent data cashback, ambassador rewards,
    and gateway fees.
    """
    # Check if optional service_transactions table exists
    has_services = False
    try:
        from sqlalchemy import inspect
        has_services = inspect(db.bind).has_table("service_transactions")
    except Exception:
        has_services = False

    # 1. REVENUE (All successful service purchases, excluding wallet funding and peer transfers)
    is_success_tx = or_(
        Transaction.status == TransactionStatus.SUCCESS,
        cast(Transaction.status, String).in_(["SUCCESS", "success", "delivered", "successful"])
    )
    is_not_wallet_tx = func.lower(cast(Transaction.tx_type, String)).notin_(["wallet_fund", "wallet_transfer"])
    is_data_tx = or_(
        Transaction.tx_type == TransactionType.DATA,
        cast(Transaction.tx_type, String).in_(["DATA", "data"])
    )
    
    revenue_amount = (
        db.query(func.sum(Transaction.amount))
        .filter(
            is_success_tx,
            is_not_wallet_tx
        )
        .scalar()
        or 0
    )
    revenue = Decimal(str(revenue_amount))

    if has_services:
        st_revenue_amount = (
            db.query(func.sum(ServiceTransaction.amount))
            .filter(
                or_(
                    ServiceTransaction.status == TransactionStatus.SUCCESS.value,
                    cast(ServiceTransaction.status, String).in_(["SUCCESS", "success", "delivered", "successful"])
                )
            )
            .scalar()
            or 0
        )
        revenue += Decimal(str(st_revenue_amount))

    # 2. COGS (Cost of Goods Sold)
    # Estimate Data COGS using current DataPlan base_price with case-insensitive plan_code match
    data_cogs_amount = (
        db.query(func.sum(func.coalesce(func.nullif(DataPlan.base_price, 0), Transaction.amount * Decimal("0.90"))))
        .select_from(Transaction)
        .outerjoin(
            DataPlan,
            or_(
                Transaction.data_plan_code == DataPlan.plan_code,
                func.lower(Transaction.data_plan_code) == func.lower(DataPlan.plan_code),
            )
        )
        .filter(
            is_success_tx,
            is_data_tx
        )
        .scalar()
        or 0
    )
    data_cogs = Decimal(str(data_cogs_amount))

    # Airtime COGS (Estimate average 3% wholesale discount)
    airtime_revenue_amount = (
        db.query(func.sum(Transaction.amount))
        .filter(
            Transaction.status == TransactionStatus.SUCCESS,
            Transaction.tx_type == TransactionType.AIRTIME
        )
        .scalar()
        or 0
    )
    if has_services:
        airtime_st_amount = (
            db.query(func.sum(ServiceTransaction.amount))
            .filter(
                ServiceTransaction.status == TransactionStatus.SUCCESS.value,
                ServiceTransaction.tx_type == "airtime"
            )
            .scalar()
            or 0
        )
        airtime_revenue_amount += airtime_st_amount

    airtime_cogs = Decimal(str(airtime_revenue_amount)) * Decimal("0.97")

    # Other services COGS (Estimate 1% wholesale discount)
    other_revenue_amount = (
        db.query(func.sum(Transaction.amount))
        .filter(
            Transaction.status == TransactionStatus.SUCCESS,
            Transaction.tx_type.in_([
                TransactionType.CABLE.value,
                TransactionType.ELECTRICITY.value,
                TransactionType.EXAM.value
            ])
        )
        .scalar()
        or 0
    )
    if has_services:
        other_st_amount = (
            db.query(func.sum(ServiceTransaction.amount))
            .filter(
                ServiceTransaction.status == TransactionStatus.SUCCESS.value,
                ServiceTransaction.tx_type.in_(["cable", "electricity", "exam"])
            )
            .scalar()
            or 0
        )
        other_revenue_amount += other_st_amount

    other_cogs = Decimal(str(other_revenue_amount)) * Decimal("0.99")

    total_cogs = data_cogs + airtime_cogs + other_cogs
    gross_margin = revenue - total_cogs

    # 3. PROMOTIONAL EXPENSE (Agent campaign milestone rewards)
    promo_expense_amount = (
        db.query(func.sum(AgentReward.amount))
        .filter(AgentReward.status == AgentRewardStatus.CREDITED)
        .scalar()
        or 0
    )
    promo_expense = Decimal(str(promo_expense_amount))

    # 4. REFERRAL EXPENSE (Ambassador first deposit rewards)
    referral_table_sum = (
        db.query(func.sum(Referral.reward_amount))
        .filter(Referral.status == ReferralStatus.REWARDED)
        .scalar()
        or 0
    )
    referral_tx_sum = (
        db.query(func.sum(Transaction.amount))
        .filter(
            Transaction.status == TransactionStatus.SUCCESS,
            Transaction.reference.like("REFERRAL_%")
        )
        .scalar()
        or 0
    )
    referral_expense = Decimal(str(max(referral_table_sum, referral_tx_sum)))

    # 5. AGENT COMMISSION EXPENSE (Agent 2% data purchase cashback)
    agent_comm_sum = (
        db.query(func.sum(Transaction.amount))
        .filter(
            Transaction.status == TransactionStatus.SUCCESS,
            or_(
                Transaction.reference.like("COMM_%"),
                Transaction.provider == "agent_commission"
            )
        )
        .scalar()
        or 0
    )
    agent_commission_expense = Decimal(str(agent_comm_sum))

    # 6. PAYMENT FEES (Estimate based ONLY on genuine external funding transactions)
    # Exclude internal wallet credits: commissions, referral rewards, admin adjustments, peer transfers, refunds
    wallet_funds = (
        db.query(Transaction.amount, Transaction.provider, Transaction.reference)
        .filter(
            Transaction.status == TransactionStatus.SUCCESS,
            Transaction.tx_type == TransactionType.WALLET_FUND,
            ~Transaction.reference.like("COMM_%"),
            ~Transaction.reference.like("REFERRAL_%"),
            ~Transaction.reference.like("ADMIN_%"),
            ~Transaction.reference.like("TRF_%"),
            ~Transaction.reference.like("REFUND_%"),
        )
        .all()
    )
    
    payment_fees = Decimal("0.0")
    for amount, provider, reference in wallet_funds:
        prov = str(provider or "").lower()
        if prov in ["transfer", "agent_commission", "admin", "system", "manual_adjustment"]:
            continue
        amt = Decimal(str(amount))
        if "billstack" in prov:
            payment_fees += amt * Decimal("0.005") # 0.5%
        elif "monnify" in prov:
            # Monnify flat fee or 1% usually, capped at 2,000 NGN
            payment_fees += min(amt * Decimal("0.01"), Decimal("2000.0"))
        elif "bank" in prov or "manual" in prov:
            continue
        else:
            # Paystack standard 1.5%
            payment_fees += amt * Decimal("0.015")

    # 7. NET PROFIT (Gross Margin - Operating Costs - Commissions - Gateway Fees)
    net_profit = (
        gross_margin
        - promo_expense
        - referral_expense
        - agent_commission_expense
        - payment_fees
    )

    # 8. CUSTOMER WALLET LIABILITY
    wallet_liability_amount = db.query(func.sum(Wallet.balance)).scalar() or 0
    wallet_liability = Decimal(str(wallet_liability_amount))

    # 9. COMPANY ASSETS (Provider Balances)
    # Strictly fetch live balances from providers that are active / ON
    assets_list = []
    total_assets = Decimal("0.0")
    try:
        from app.core.config import get_settings
        from app.models.integration import IntegrationProvider
        app_settings = get_settings()

        # Query all integration providers from database
        db_providers = {}
        try:
            if inspect(db.bind).has_table("integration_providers"):
                db_providers = {
                    p.identifier.lower(): p
                    for p in db.query(IntegrationProvider).all()
                }
        except Exception as e:
            logger.warning(f"Could not load integration_providers table: {e}")

        # 1. SMEPlug (Check if active in DB or has API key in settings)
        sme_p = db_providers.get("smeplug")
        sme_active = bool(
            (sme_p and sme_p.is_active and sme_p.api_key) or
            (app_settings.smeplug_api_key and (sme_p is None or (sme_p and sme_p.is_active)))
        )
        if sme_active:
            try:
                from app.providers.smeplug_provider import SMEPlugProvider
                sme = SMEPlugProvider(db=db)
                sme_bal = sme.get_balance()
                if isinstance(sme_bal, (int, float)) and not isinstance(sme_bal, bool) and sme_bal >= 0:
                    assets_list.append({"name": "SMEPlug", "balance": float(sme_bal), "type": "PROVIDER_WALLET"})
                    total_assets += Decimal(str(sme_bal))
            except Exception as e:
                logger.warning(f"Could not load SMEPlug balance: {e}")

        # 2. Amigo (Only query if explicitly active in DB with valid API key)
        amigo_p = db_providers.get("amigo")
        amigo_active = bool(amigo_p and amigo_p.is_active and amigo_p.api_key and amigo_p.api_key.strip())
        if amigo_active:
            try:
                from app.services.amigo import AmigoClient
                amigo = AmigoClient(api_key=amigo_p.api_key if amigo_p else None, base_url=amigo_p.base_url if amigo_p else None)
                amigo_bal = amigo.get_balance()
                if isinstance(amigo_bal, (int, float)) and not isinstance(amigo_bal, bool) and amigo_bal >= 0:
                    assets_list.append({"name": "Amigo", "balance": float(amigo_bal), "type": "PROVIDER_WALLET"})
                    total_assets += Decimal(str(amigo_bal))
            except Exception as e:
                logger.warning(f"Could not load Amigo balance: {e}")

        # 3. ClubKonnect (Only query if explicitly active in DB with valid credentials)
        ck_p = db_providers.get("clubkonnect")
        ck_active = bool(ck_p and ck_p.is_active and ck_p.api_key and ck_p.api_key.strip())
        if ck_active:
            try:
                from app.services.bills import ClubKonnectBillsProvider
                club = ClubKonnectBillsProvider()
                club_bal = club.get_balance()
                if isinstance(club_bal, (int, float)) and not isinstance(club_bal, bool) and club_bal >= 0:
                    assets_list.append({"name": "ClubKonnect", "balance": float(club_bal), "type": "PROVIDER_WALLET"})
                    total_assets += Decimal(str(club_bal))
            except Exception as e:
                logger.warning(f"Could not load ClubKonnect balance: {e}")

        # 4. Any other active providers in database (e.g. AutoSync, BoltNet, Telecom Abode)
        for ident, p in db_providers.items():
            if ident in ["smeplug", "amigo", "clubkonnect"]:
                continue
            if p.is_active and p.api_key and p.api_key.strip():
                bal = float(p.balance or 0.0)
                if bal >= 0:
                    assets_list.append({"name": p.name or ident.upper(), "balance": bal, "type": "PROVIDER_WALLET"})
                    total_assets += Decimal(str(bal))

    except Exception as e:
        logger.warning(f"Could not fetch live provider balances: {e}")

    # 10. OWNER CAPITAL & DEBTS
    has_ledger_table = False
    try:
        has_ledger_table = inspect(db.bind).has_table("financial_ledger")
    except Exception:
        has_ledger_table = False

    owner_capital_amount = 0
    owner_withdrawal_amount = 0
    business_debts_amount = 0
    receivables_amount = 0

    if has_ledger_table:
        try:
            owner_capital_amount = db.query(func.sum(FinancialLedger.amount)).filter(
                func.lower(cast(FinancialLedger.category, String)) == "owner_capital",
                func.lower(cast(FinancialLedger.entry_type, String)) == "credit"
            ).scalar() or 0
            
            owner_withdrawal_amount = db.query(func.sum(FinancialLedger.amount)).filter(
                func.lower(cast(FinancialLedger.category, String)) == "owner_withdrawal",
                func.lower(cast(FinancialLedger.entry_type, String)) == "debit"
            ).scalar() or 0
            
            business_debts_amount = db.query(func.sum(FinancialLedger.amount)).filter(
                func.lower(cast(FinancialLedger.category, String)) == "business_debt",
                func.lower(cast(FinancialLedger.entry_type, String)) == "credit"
            ).scalar() or 0
            
            receivables_amount = db.query(func.sum(FinancialLedger.amount)).filter(
                func.lower(cast(FinancialLedger.category, String)) == "receivable",
                func.lower(cast(FinancialLedger.entry_type, String)) == "debit"
            ).scalar() or 0
        except Exception as exc:
            logger.warning(f"Could not calculate financial ledger balances: {exc}")

    owner_capital = Decimal(str(owner_capital_amount)) - Decimal(str(owner_withdrawal_amount))
    business_debts = Decimal(str(business_debts_amount))
    receivables = Decimal(str(receivables_amount))
    
    # 11. RECONCILIATION
    # Total Assets = Company Assets (Bank/Provider) + Receivables
    # Total Liabilities = Wallet Liability + Business Debts
    # Equity = Owner Capital + Net Profit
    # Expected Assets = Liabilities + Equity
    net_equity = owner_capital + net_profit
    expected_assets = wallet_liability + business_debts + net_equity
    observed_assets = total_assets + receivables
    reconciliation_gap = observed_assets - expected_assets

    return {
        "revenue": {
            "actual": float(revenue)
        },
        "cogs": {
            "total": float(total_cogs),
            "data_cogs_estimated": float(data_cogs),
            "airtime_cogs_estimated": float(airtime_cogs),
            "other_cogs_estimated": float(other_cogs),
            "is_estimated": True
        },
        "gross_margin": float(gross_margin),
        "promotional_expense": float(promo_expense),
        "referral_expense": float(referral_expense),
        "agent_commission_expense": float(agent_commission_expense),
        "payment_fees": {
            "total": float(payment_fees),
            "is_estimated": True
        },
        "net_profit": float(net_profit),
        "is_net_profit_estimated": True, # since COGS and Fees are estimated
        "customer_wallet_liability": float(wallet_liability),
        "company_assets": {
            "total": float(total_assets),
            "providers": assets_list
        },
        "owner_capital": float(owner_capital),
        "business_debts": float(business_debts),
        "receivables": float(receivables),
        "net_equity": float(net_equity),
        "reconciliation_gap": float(reconciliation_gap)
    }

@router.post("/ledger", response_model=FinancialLedgerResponse)
def create_ledger_entry(
    entry: FinancialLedgerCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin)
) -> Any:
    """
    Manually add a financial ledger entry (e.g. Owner Capital, Debt)
    """
    try:
        import secrets
        ref = entry.reference or f"FIN_{secrets.token_hex(6).upper()}"

        inspector = inspect(db.bind)
        if not inspector.has_table("financial_ledger"):
            try:
                FinancialLedger.__table__.create(db.bind, checkfirst=True)
            except Exception:
                pass
            inspector = inspect(db.bind)

        cols = {c["name"] for c in inspector.get_columns("financial_ledger")}
        
        # Self-heal missing columns if necessary
        missing = []
        if "created_by_id" not in cols:
            missing.append("ADD COLUMN IF NOT EXISTS created_by_id INTEGER REFERENCES users(id)")
        if "source_id" not in cols:
            missing.append("ADD COLUMN IF NOT EXISTS source_id VARCHAR(64)")
        if "brand" not in cols:
            missing.append("ADD COLUMN IF NOT EXISTS brand VARCHAR(32) DEFAULT 'meledata'")
        
        if missing:
            try:
                with db.bind.begin() as conn:
                    for sql_clause in missing:
                        try:
                            conn.execute(text(f"ALTER TABLE financial_ledger {sql_clause}"))
                        except Exception:
                            pass
                inspector = inspect(db.bind)
                cols = {c["name"] for c in inspector.get_columns("financial_ledger")}
            except Exception as e:
                logger.warning(f"Could not auto-add columns to financial_ledger: {e}")

        created_by_val = user.id if "created_by_id" in cols else None

        cat_val = entry.category.value if hasattr(entry.category, "value") else str(entry.category)
        entry_val = entry.entry_type.value if hasattr(entry.entry_type, "value") else str(entry.entry_type)

        target_brand = (
            request.headers.get("x-app-brand")
            or getattr(user, "brand", None)
            or "meledata"
        ).strip().lower()

        insert_kwargs = {
            "reference": ref,
            "source": "manual",
            "status": "completed",
            "category": cat_val,
            "entry_type": entry_val,
            "amount": Decimal(str(entry.amount)),
            "party": entry.party,
            "description": entry.description,
            "created_by_id": created_by_val,
        }
        if "brand" in cols:
            insert_kwargs["brand"] = target_brand

        db_entry = FinancialLedger(**insert_kwargs)
        db.add(db_entry)
        db.commit()
        db.refresh(db_entry)

        return {
            "id": db_entry.id,
            "category": str(cat_val),
            "entry_type": str(entry_val),
            "amount": float(entry.amount),
            "party": entry.party,
            "description": entry.description,
            "reference": ref,
            "status": "completed",
            "created_at": db_entry.created_at or datetime.now(timezone.utc),
        }
    except Exception as exc:
        db.rollback()
        logger.error(f"Error creating financial ledger entry: {exc}", exc_info=True)
        raise HTTPException(status_code=400, detail=f"Failed to record ledger entry: {str(exc)}")

@router.get("/ledger", response_model=List[FinancialLedgerResponse])
def get_ledger(
    skip: int = 0,
    limit: int = 100,
    category: Optional[FinancialCategory] = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin)
) -> Any:
    try:
        if not inspect(db.bind).has_table("financial_ledger"):
            return []
        query = db.query(FinancialLedger)
        if category:
            cat_val = category.value if hasattr(category, "value") else str(category)
            query = query.filter(FinancialLedger.category == cat_val)
        
        entries = query.order_by(FinancialLedger.created_at.desc()).offset(skip).limit(limit).all()
        return [
            {
                "id": e.id,
                "category": str(e.category),
                "entry_type": str(e.entry_type),
                "amount": float(e.amount),
                "party": e.party,
                "description": e.description,
                "reference": e.reference,
                "status": e.status,
                "created_at": e.created_at,
            }
            for e in entries
        ]
    except Exception as exc:
        logger.warning(f"Error fetching financial ledger: {exc}")
        return []
    

@router.get("/leak-audit")
def get_leak_audit(
    db: Session = Depends(get_db),
    user: User = Depends(require_admin)
) -> Any:
    """
    Forensic leak auditor scanning all user accounts for negative equity (spent > funded)
    and phantom wallet balances. Accurately handles service purchases, agent commissions,
    referral bonuses, refunds, and wallet transfers.
    """
    has_services = False
    try:
        from sqlalchemy import inspect
        has_services = inspect(db.bind).has_table("service_transactions")
    except Exception:
        has_services = False

    total_deposits = db.query(func.coalesce(func.sum(Transaction.amount), 0)).filter(
        Transaction.tx_type == TransactionType.WALLET_FUND,
        Transaction.status == TransactionStatus.SUCCESS
    ).scalar() or 0

    service_types = [
        TransactionType.DATA.value,
        TransactionType.AIRTIME.value,
        TransactionType.CABLE.value,
        TransactionType.ELECTRICITY.value,
        TransactionType.EXAM.value
    ]
    total_spends = db.query(func.coalesce(func.sum(Transaction.amount), 0)).filter(
        Transaction.tx_type.in_(service_types),
        Transaction.status == TransactionStatus.SUCCESS
    ).scalar() or 0

    if has_services:
        st_spends = db.query(func.coalesce(func.sum(ServiceTransaction.amount), 0)).filter(
            ServiceTransaction.status == TransactionStatus.SUCCESS.value
        ).scalar() or 0
        total_spends += st_spends

    total_wallet_liability = db.query(func.coalesce(func.sum(Wallet.balance), 0)).scalar() or 0

    # User reconciliation query
    if has_services:
        spends_subquery = """
            SELECT user_id, amount
            FROM transactions
            WHERE UPPER(CAST(status AS TEXT)) = 'SUCCESS'
              AND UPPER(CAST(tx_type AS TEXT)) IN ('DATA', 'AIRTIME', 'CABLE', 'ELECTRICITY', 'EXAM', 'WALLET_TRANSFER')
            UNION ALL
            SELECT user_id, amount
            FROM service_transactions
            WHERE LOWER(CAST(status AS TEXT)) = 'success'
        """
    else:
        spends_subquery = """
            SELECT user_id, amount
            FROM transactions
            WHERE UPPER(CAST(status AS TEXT)) = 'SUCCESS'
              AND UPPER(CAST(tx_type AS TEXT)) IN ('DATA', 'AIRTIME', 'CABLE', 'ELECTRICITY', 'EXAM', 'WALLET_TRANSFER')
        """

    user_funding_sql = text(f"""
        SELECT 
            u.id as user_id,
            u.email,
            u.phone_number,
            u.full_name,
            COALESCE(w.balance, 0) as current_balance,
            COALESCE(f.total_funded, 0) as total_funded,
            COALESCE(s.total_spent, 0) as total_spent,
            COALESCE(r.total_refunded, 0) as total_refunded,
            COALESCE(c.total_commission, 0) as total_commission
        FROM users u
        LEFT JOIN wallets w ON w.user_id = u.id
        LEFT JOIN (
            SELECT user_id, SUM(amount) as total_funded
            FROM transactions
            WHERE UPPER(CAST(tx_type AS TEXT)) = 'WALLET_FUND' AND UPPER(CAST(status AS TEXT)) = 'SUCCESS'
            GROUP BY user_id
        ) f ON f.user_id = u.id
        LEFT JOIN (
            SELECT user_id, SUM(amount) as total_spent
            FROM ({spends_subquery}) all_spends
            GROUP BY user_id
        ) s ON s.user_id = u.id
        LEFT JOIN (
            SELECT user_id, SUM(amount) as total_refunded
            FROM transactions
            WHERE UPPER(CAST(status AS TEXT)) = 'REFUNDED'
            GROUP BY user_id
        ) r ON r.user_id = u.id
        LEFT JOIN (
            SELECT user_id, SUM(amount) as total_commission
            FROM transactions
            WHERE UPPER(CAST(status AS TEXT)) = 'SUCCESS'
              AND (reference LIKE 'COMM_%' OR provider = 'agent_commission')
            GROUP BY user_id
        ) c ON c.user_id = u.id
    """)

    rows = db.execute(user_funding_sql).fetchall()

    overspent_users = []
    phantom_balance_users = []
    total_real_cash_lost = Decimal("0")
    total_phantom_balance_in_wallets = Decimal("0")

    for row in rows:
        curr_bal = Decimal(str(row.current_balance or 0))
        funded = Decimal(str(row.total_funded or 0))
        spent = Decimal(str(row.total_spent or 0))
        refunded = Decimal(str(row.total_refunded or 0))
        comm = Decimal(str(row.total_commission or 0))

        expected_balance = funded - spent
        discrepancy = curr_bal - expected_balance

        if spent > funded:
            net_loss = spent - funded
            total_real_cash_lost += net_loss
            overspent_users.append({
                "user_id": row.user_id,
                "email": row.email,
                "phone_number": row.phone_number,
                "full_name": row.full_name,
                "funded": float(funded),
                "spent": float(spent),
                "current_balance": float(curr_bal),
                "overspent": float(net_loss),
                "refunded": float(refunded),
                "commission_earned": float(comm)
            })

        if discrepancy > Decimal("5.00"):
            phantom_part = min(curr_bal, discrepancy)
            if phantom_part > Decimal("0"):
                total_phantom_balance_in_wallets += phantom_part
                phantom_balance_users.append({
                    "user_id": row.user_id,
                    "email": row.email,
                    "phone_number": row.phone_number,
                    "full_name": row.full_name,
                    "current_balance": float(curr_bal),
                    "expected_balance": float(expected_balance),
                    "phantom_balance": float(phantom_part),
                    "funded": float(funded),
                    "spent": float(spent),
                    "commission_earned": float(comm)
                })

    overspent_users.sort(key=lambda x: x["overspent"], reverse=True)
    phantom_balance_users.sort(key=lambda x: x["phantom_balance"], reverse=True)

    return {
        "summary": {
            "total_deposits": float(total_deposits),
            "total_spends": float(total_spends),
            "total_wallet_liability": float(total_wallet_liability),
            "total_real_cash_lost": float(total_real_cash_lost),
            "total_phantom_balance_in_wallets": float(total_phantom_balance_in_wallets),
            "total_leakage": float(total_real_cash_lost + total_phantom_balance_in_wallets),
            "overspent_users_count": len(overspent_users),
            "phantom_balance_users_count": len(phantom_balance_users),
        },
        "top_overspent_users": overspent_users[:50],
        "top_phantom_balance_users": phantom_balance_users[:50]
    }

