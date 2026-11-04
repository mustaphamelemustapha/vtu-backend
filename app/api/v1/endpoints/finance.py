import logging
from decimal import Decimal
from typing import Any, List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, text
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
    category: FinancialCategory
    entry_type: EntryType
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
    created_at: datetime
    
    class Config:
        orm_mode = True

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
    service_tx_types = [
        TransactionType.DATA.value,
        TransactionType.AIRTIME.value,
        TransactionType.CABLE.value,
        TransactionType.ELECTRICITY.value,
        TransactionType.EXAM.value
    ]
    
    revenue_amount = (
        db.query(func.sum(Transaction.amount))
        .filter(
            Transaction.status == TransactionStatus.SUCCESS,
            or_(
                Transaction.tx_type.in_(service_tx_types),
                Transaction.tx_type.notin_([TransactionType.WALLET_FUND, TransactionType.WALLET_TRANSFER])
            )
        )
        .scalar()
        or 0
    )
    revenue = Decimal(str(revenue_amount))

    if has_services:
        st_revenue_amount = (
            db.query(func.sum(ServiceTransaction.amount))
            .filter(
                ServiceTransaction.status == TransactionStatus.SUCCESS.value
            )
            .scalar()
            or 0
        )
        revenue += Decimal(str(st_revenue_amount))

    # 2. COGS (Cost of Goods Sold)
    # Estimate Data COGS using current DataPlan base_price with case-insensitive plan_code match
    data_cogs_amount = (
        db.query(func.sum(func.coalesce(func.nullif(DataPlan.base_price, 0), Transaction.amount)))
        .select_from(Transaction)
        .outerjoin(
            DataPlan,
            func.lower(Transaction.data_plan_code) == func.lower(DataPlan.plan_code)
        )
        .filter(
            Transaction.status == TransactionStatus.SUCCESS,
            Transaction.tx_type == TransactionType.DATA
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
    # Fetch live balances from supported providers
    assets_list = []
    total_assets = Decimal("0.0")
    try:
        from app.providers.smeplug_provider import SMEPlugProvider
        from app.services.amigo import AmigoClient
        from app.services.bills import ClubKonnectBillsProvider
        
        # 1. SMEPlug
        try:
            sme = SMEPlugProvider()
            sme_bal = sme.get_balance()
            if isinstance(sme_bal, (int, float)):
                assets_list.append({"name": "SMEPlug", "balance": float(sme_bal), "type": "PROVIDER_WALLET"})
                total_assets += Decimal(str(sme_bal))
            else:
                assets_list.append({"name": "SMEPlug", "balance": 0.0, "type": "ERROR", "error": str(sme_bal)})
        except Exception as e:
            assets_list.append({"name": "SMEPlug", "balance": 0.0, "type": "ERROR", "error": str(e)})

        # 2. Amigo
        try:
            amigo = AmigoClient()
            amigo_bal = amigo.get_balance()
            if isinstance(amigo_bal, (int, float)):
                assets_list.append({"name": "Amigo", "balance": float(amigo_bal), "type": "PROVIDER_WALLET"})
                total_assets += Decimal(str(amigo_bal))
            else:
                assets_list.append({"name": "Amigo", "balance": 0.0, "type": "ERROR", "error": str(amigo_bal)})
        except Exception as e:
            assets_list.append({"name": "Amigo", "balance": 0.0, "type": "ERROR", "error": str(e)})

        # 3. ClubKonnect
        try:
            club = ClubKonnectBillsProvider()
            club_bal = club.get_balance()
            if isinstance(club_bal, (int, float)):
                assets_list.append({"name": "ClubKonnect", "balance": float(club_bal), "type": "PROVIDER_WALLET"})
                total_assets += Decimal(str(club_bal))
            else:
                assets_list.append({"name": "ClubKonnect", "balance": 0.0, "type": "ERROR", "error": str(club_bal)})
        except Exception as e:
            assets_list.append({"name": "ClubKonnect", "balance": 0.0, "type": "ERROR", "error": str(e)})

    except Exception as e:
        logger.warning(f"Could not fetch live provider balances: {e}")
        assets_list.append({"name": "Live Balances", "balance": 0.0, "type": "ERROR", "error": str(e)})

    # 10. OWNER CAPITAL & DEBTS
    owner_capital_amount = db.query(func.sum(FinancialLedger.amount)).filter(
        FinancialLedger.category == FinancialCategory.OWNER_CAPITAL,
        FinancialLedger.entry_type == EntryType.CREDIT
    ).scalar() or 0
    
    owner_withdrawal_amount = db.query(func.sum(FinancialLedger.amount)).filter(
        FinancialLedger.category == FinancialCategory.OWNER_WITHDRAWAL,
        FinancialLedger.entry_type == EntryType.DEBIT
    ).scalar() or 0
    
    owner_capital = Decimal(str(owner_capital_amount)) - Decimal(str(owner_withdrawal_amount))
    
    business_debts_amount = db.query(func.sum(FinancialLedger.amount)).filter(
        FinancialLedger.category == FinancialCategory.BUSINESS_DEBT,
        FinancialLedger.entry_type == EntryType.CREDIT
    ).scalar() or 0
    business_debts = Decimal(str(business_debts_amount))
    
    receivables_amount = db.query(func.sum(FinancialLedger.amount)).filter(
        FinancialLedger.category == FinancialCategory.RECEIVABLE,
        FinancialLedger.entry_type == EntryType.DEBIT
    ).scalar() or 0
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
    db: Session = Depends(get_db),
    user: User = Depends(require_admin)
) -> Any:
    """
    Manually add a financial ledger entry (e.g. Owner Capital, Debt)
    """
    import secrets
    ref = entry.reference or f"FIN_{secrets.token_hex(6).upper()}"
    
    db_entry = FinancialLedger(
        reference=ref,
        source="manual",
        category=entry.category,
        entry_type=entry.entry_type,
        amount=Decimal(str(entry.amount)),
        party=entry.party,
        description=entry.description,
        created_by_id=user.id
    )
    db.add(db_entry)
    db.commit()
    db.refresh(db_entry)
    return db_entry

@router.get("/ledger", response_model=List[FinancialLedgerResponse])
def get_ledger(
    skip: int = 0,
    limit: int = 100,
    category: Optional[FinancialCategory] = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin)
) -> Any:
    query = db.query(FinancialLedger)
    if category:
        query = query.filter(FinancialLedger.category == category)
    
    return query.order_by(FinancialLedger.created_at.desc()).offset(skip).limit(limit).all()
    

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

