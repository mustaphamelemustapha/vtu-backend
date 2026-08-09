import logging
from decimal import Decimal
from typing import Any, List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.core.database import get_db
from app.dependencies import get_current_active_user, require_admin
from app.models import (
    Transaction, TransactionStatus, TransactionType,
    DataPlan, AgentReward, AgentRewardStatus,
    Referral, ReferralStatus, Wallet,
    FinancialLedger, FinancialCategory, EntryType,
    User
)

router = APIRouter()
logger = logging.getLogger(__name__)

# --- Pydantic Schemas ---

class FinanceOverviewResponse(BaseModel):
    revenue: dict # { "actual": float }
    cogs: dict # { "total": float, "data_cogs_estimated": float, "airtime_cogs_estimated": float, "is_estimated": bool }
    gross_margin: float
    promotional_expense: float
    referral_expense: float
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
    """
    # 1. REVENUE (Exclude WALLET_FUND and WALLET_TRANSFER)
    service_tx_types = [
        TransactionType.DATA,
        TransactionType.AIRTIME,
        TransactionType.CABLE,
        TransactionType.ELECTRICITY,
        TransactionType.EXAM
    ]
    
    revenue_amount = (
        db.query(func.sum(Transaction.amount))
        .filter(
            Transaction.status == TransactionStatus.SUCCESS,
            Transaction.tx_type.in_(service_tx_types)
        )
        .scalar()
        or 0
    )
    revenue = Decimal(str(revenue_amount))

    # 2. COGS (Cost of Goods Sold)
    # Estimate Data COGS using current DataPlan base_price
    data_cogs_amount = (
        db.query(func.sum(func.coalesce(DataPlan.base_price, Transaction.amount)))
        .select_from(Transaction)
        .outerjoin(DataPlan, Transaction.data_plan_code == DataPlan.plan_code)
        .filter(
            Transaction.status == TransactionStatus.SUCCESS,
            Transaction.tx_type == TransactionType.DATA
        )
        .scalar()
        or 0
    )
    data_cogs = Decimal(str(data_cogs_amount))

    # Estimate Airtime COGS (Assuming average 3% discount across networks if exact cost isn't logged)
    # Using 0.97 * amount for estimation.
    airtime_revenue_amount = (
        db.query(func.sum(Transaction.amount))
        .filter(
            Transaction.status == TransactionStatus.SUCCESS,
            Transaction.tx_type == TransactionType.AIRTIME
        )
        .scalar()
        or 0
    )
    airtime_cogs = Decimal(str(airtime_revenue_amount)) * Decimal("0.97")

    # Other services COGS (Assume cost = amount for now, or 1% discount)
    other_revenue_amount = (
        db.query(func.sum(Transaction.amount))
        .filter(
            Transaction.status == TransactionStatus.SUCCESS,
            Transaction.tx_type.in_([TransactionType.CABLE, TransactionType.ELECTRICITY, TransactionType.EXAM])
        )
        .scalar()
        or 0
    )
    other_cogs = Decimal(str(other_revenue_amount)) * Decimal("0.99") # Estimate 1% discount

    total_cogs = data_cogs + airtime_cogs + other_cogs

    gross_margin = revenue - total_cogs

    # 3. PROMOTIONAL EXPENSE
    promo_expense_amount = (
        db.query(func.sum(AgentReward.amount))
        .filter(AgentReward.status == AgentRewardStatus.CREDITED)
        .scalar()
        or 0
    )
    promo_expense = Decimal(str(promo_expense_amount))

    # 4. REFERRAL EXPENSE
    referral_expense_amount = (
        db.query(func.sum(Referral.reward_amount))
        .filter(Referral.status == ReferralStatus.REWARDED)
        .scalar()
        or 0
    )
    referral_expense = Decimal(str(referral_expense_amount))

    # 5. PAYMENT FEES (Estimate based on WALLET_FUND transactions)
    # 0.5% for Billstack, assuming all unlabelled are 1.5% like Paystack
    wallet_funds = db.query(Transaction.amount, Transaction.provider).filter(
        Transaction.status == TransactionStatus.SUCCESS,
        Transaction.tx_type == TransactionType.WALLET_FUND
    ).all()
    
    payment_fees = Decimal("0.0")
    for amount, provider in wallet_funds:
        amt = Decimal(str(amount))
        if provider and "billstack" in str(provider).lower():
            payment_fees += amt * Decimal("0.005")
        elif provider and "monnify" in str(provider).lower():
            # Monnify flat fee or 1% usually, let's assume 1% capped
            payment_fees += min(amt * Decimal("0.01"), Decimal("2000.0"))
        else:
            # Paystack 1.5%
            payment_fees += amt * Decimal("0.015")

    # 6. NET PROFIT
    net_profit = gross_margin - promo_expense - referral_expense - payment_fees

    # 7. CUSTOMER WALLET LIABILITY
    wallet_liability_amount = db.query(func.sum(Wallet.balance)).scalar() or 0
    wallet_liability = Decimal(str(wallet_liability_amount))

    # 8. COMPANY ASSETS (Provider Balances)
    # This requires fetching live balances.
    assets_list = []
    total_assets = Decimal("0.0")
    try:
        from app.services.bills import get_bills_provider
        from app.services.amigo import AmigoService
        from app.services.clubkonnect import ClubKonnect
        
        provider = get_bills_provider()
        sme_bal = provider.get_balance()
        assets_list.append({"name": "SMEPlug", "balance": float(sme_bal), "type": "PROVIDER_WALLET"})
        total_assets += sme_bal
        
        amigo = AmigoService()
        amigo_bal = amigo.get_balance()
        assets_list.append({"name": "Amigo", "balance": float(amigo_bal), "type": "PROVIDER_WALLET"})
        total_assets += amigo_bal
        
        club = ClubKonnect()
        club_bal = club.get_balance()
        assets_list.append({"name": "ClubKonnect", "balance": float(club_bal), "type": "PROVIDER_WALLET"})
        total_assets += club_bal
    except Exception as e:
        logger.warning(f"Could not fetch live provider balances: {e}")
        assets_list.append({"name": "Live Balances", "balance": 0.0, "type": "ERROR", "error": str(e)})

    # 9. OWNER CAPITAL & DEBTS
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
    
    # 10. RECONCILIATION
    # Total Assets = Company Assets (Bank/Provider) + Receivables
    # Total Liabilities = Wallet Liability + Business Debts
    # Equity = Owner Capital + Net Profit
    # Expected Assets = Liabilities + Equity
    expected_assets = wallet_liability + business_debts + owner_capital + net_profit
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
            "is_estimated": True
        },
        "gross_margin": float(gross_margin),
        "promotional_expense": float(promo_expense),
        "referral_expense": float(referral_expense),
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
        "net_equity": float(owner_capital + net_profit),
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
