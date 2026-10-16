#!/usr/bin/env python3
"""
Platform-Wide Financial Audit & Cash Leakage Tracker
====================================================
This script performs a forensic accounting audit across all users and transactions:
1. Total Cash Deposited (Monnify / Paystack / Bank Transfers)
2. Total Services Delivered (Data, Airtime, Utilities)
3. Total Outstanding Customer Wallet Liability (Float)
4. Detection of Fallback / Phantom Refund Leakage across all accounts:
   - Users who spent more than they ever deposited (Real cash lost)
   - Phantom wallet balances currently waiting to be spent
5. Net Business Position: Where your money actually is.
"""

import os
import sys
from decimal import Decimal
from datetime import datetime, timezone

# Add backend directory to path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from sqlalchemy import func, text
from app.core.database import SessionLocal
from app.models.wallet import Wallet
from app.models.transaction import Transaction, TransactionStatus, TransactionType

def format_ngn(val):
    if val is None:
        val = 0.0
    return f"₦{float(val):,.2f}"

def run_audit():
    db = SessionLocal()
    print("=" * 75)
    print("   MELE DATA / VTU PLATFORM FORENSIC FINANCIAL & LEAKAGE AUDIT")
    print(f"   Generated on: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 75)

    try:
        # 1. High Level Platform Totals
        print("\n[1/5] CALCULATING PLATFORM-WIDE LIFETIME TOTALS...")
        
        # Total deposits (Wallet Funds)
        total_deposits = db.query(func.coalesce(func.sum(Transaction.amount), 0)).filter(
            Transaction.tx_type == TransactionType.WALLET_FUND,
            Transaction.status == TransactionStatus.SUCCESS
        ).scalar() or Decimal("0")
        
        total_deposits_count = db.query(func.count(Transaction.id)).filter(
            Transaction.tx_type == TransactionType.WALLET_FUND,
            Transaction.status == TransactionStatus.SUCCESS
        ).scalar() or 0

        # Total successful service spends
        service_types = [
            TransactionType.DATA,
            TransactionType.AIRTIME,
            TransactionType.CABLE,
            TransactionType.ELECTRICITY,
            TransactionType.EXAM
        ]
        total_spends = db.query(func.coalesce(func.sum(Transaction.amount), 0)).filter(
            Transaction.tx_type.in_(service_types),
            Transaction.status == TransactionStatus.SUCCESS
        ).scalar() or Decimal("0")

        total_spends_count = db.query(func.count(Transaction.id)).filter(
            Transaction.tx_type.in_(service_types),
            Transaction.status == TransactionStatus.SUCCESS
        ).scalar() or 0

        # Total refunded transactions
        total_refunds = db.query(func.coalesce(func.sum(Transaction.amount), 0)).filter(
            Transaction.tx_type.in_(service_types),
            Transaction.status == TransactionStatus.REFUNDED
        ).scalar() or Decimal("0")

        total_refunds_count = db.query(func.count(Transaction.id)).filter(
            Transaction.tx_type.in_(service_types),
            Transaction.status == TransactionStatus.REFUNDED
        ).scalar() or 0

        # Current total customer wallet balances (Liability)
        total_wallet_liability = db.query(func.coalesce(func.sum(Wallet.balance), 0)).scalar() or Decimal("0")
        total_wallets_count = db.query(func.count(Wallet.id)).scalar() or 0

        print(f"  • Total Customer Cash Deposits:      {format_ngn(total_deposits):>16} ({total_deposits_count:,} deposits)")
        print(f"  • Total Successful Services Sold:    {format_ngn(total_spends):>16} ({total_spends_count:,} orders delivered)")
        print(f"  • Total Refunded Order Value:        {format_ngn(total_refunds):>16} ({total_refunds_count:,} orders marked refunded)")
        print(f"  • Current Customer Wallet Liability: {format_ngn(total_wallet_liability):>16} ({total_wallets_count:,} user wallets)")

        # 2. Per-User Reconciliation & Leakage Scan
        print("\n[2/5] SCANNING ALL USER ACCOUNTS FOR PHANTOM CREDITS & NEGATIVE EQUITY...")
        
        # Subquery for user funding totals
        user_funding_sql = text("""
            SELECT 
                u.id as user_id,
                u.email,
                u.phone_number,
                u.full_name,
                COALESCE(w.balance, 0) as current_balance,
                COALESCE(f.total_funded, 0) as total_funded,
                COALESCE(s.total_spent, 0) as total_spent,
                COALESCE(r.total_refunded, 0) as total_refunded
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
                FROM transactions
                WHERE UPPER(CAST(tx_type AS TEXT)) IN ('DATA', 'AIRTIME', 'CABLE', 'ELECTRICITY', 'EXAM') 
                  AND UPPER(CAST(status AS TEXT)) = 'SUCCESS'
                GROUP BY user_id
            ) s ON s.user_id = u.id
            LEFT JOIN (
                SELECT user_id, SUM(amount) as total_refunded
                FROM transactions
                WHERE UPPER(CAST(status AS TEXT)) = 'REFUNDED'
                GROUP BY user_id
            ) r ON r.user_id = u.id
        """)

        rows = db.execute(user_funding_sql).fetchall()

        overspent_users = []
        phantom_balance_users = []
        total_real_cash_lost = Decimal("0")
        total_phantom_balance_in_wallets = Decimal("0")

        for row in rows:
            u_id = row.user_id
            email = row.email or "N/A"
            phone = row.phone_number or "N/A"
            name = row.full_name or "N/A"
            curr_bal = Decimal(str(row.current_balance or 0))
            funded = Decimal(str(row.total_funded or 0))
            spent = Decimal(str(row.total_spent or 0))
            refunded = Decimal(str(row.total_refunded or 0))

            expected_balance = funded - spent
            discrepancy = curr_bal - expected_balance

            # If spent > funded, user consumed more than they deposited
            if spent > funded:
                net_loss = spent - funded
                total_real_cash_lost += net_loss
                overspent_users.append({
                    "id": u_id,
                    "email": email,
                    "phone": phone,
                    "name": name,
                    "funded": funded,
                    "spent": spent,
                    "current_balance": curr_bal,
                    "overspent": net_loss,
                    "refunded": refunded
                })

            # If discrepancy > 0 and wallet has positive balance
            if discrepancy > Decimal("5.00"):
                phantom_part = min(curr_bal, discrepancy)
                if phantom_part > Decimal("0"):
                    total_phantom_balance_in_wallets += phantom_part
                    phantom_balance_users.append({
                        "id": u_id,
                        "email": email,
                        "phone": phone,
                        "name": name,
                        "current_balance": curr_bal,
                        "expected_balance": expected_balance,
                        "phantom_balance": phantom_part,
                        "funded": funded,
                        "spent": spent
                    })

        # Sort by largest overspent
        overspent_users.sort(key=lambda x: x["overspent"], reverse=True)
        phantom_balance_users.sort(key=lambda x: x["phantom_balance"], reverse=True)

        print(f"\n  • Users who spent MORE than they ever deposited: {len(overspent_users):,} users")
        print(f"  • Total Cash Lost to Over-delivery (Already consumed): {format_ngn(total_real_cash_lost)}")
        print(f"  • Users holding Phantom Balances in their wallets:   {len(phantom_balance_users):,} users")
        print(f"  • Total Phantom Balance sitting in Customer Wallets:   {format_ngn(total_phantom_balance_in_wallets)}")
        total_leakage = total_real_cash_lost + total_phantom_balance_in_wallets
        print(f"  ==> TOTAL CASH LEAKAGE (Lost + Vulnerable in Wallets): {format_ngn(total_leakage)}")

        # 3. Top Overspent Users Table
        if overspent_users:
            print("\n[3/5] TOP USERS WITH REAL NEGATIVE EQUITY (Consumed free services):")
            print("-" * 90)
            print(f"{'User ID':<8} | {'Email / Phone':<28} | {'Funded':<12} | {'Delivered':<12} | {'Net Loss':<12} | {'Wallet Bal':<10}")
            print("-" * 90)
            for u in overspent_users[:15]:
                ident = f"{u['phone']} ({u['email'][:12]})" if u['phone'] != 'N/A' else u['email'][:26]
                print(f"{u['id']:<8} | {ident:<28} | {format_ngn(u['funded']):<12} | {format_ngn(u['spent']):<12} | -{format_ngn(u['overspent']):<11} | {format_ngn(u['current_balance']):<10}")

        # 4. Top Phantom Balances Table (Actionable for recovery)
        if phantom_balance_users:
            print("\n[4/5] TOP ACCOUNTS HOLDING UNEARNED PHANTOM WALLET BALANCES (Recoverable):")
            print("-" * 90)
            print(f"{'User ID':<8} | {'Email / Phone':<28} | {'Current Bal':<12} | {'True Bal':<12} | {'Phantom Surplus':<14}")
            print("-" * 90)
            for u in phantom_balance_users[:15]:
                ident = f"{u['phone']} ({u['email'][:12]})" if u['phone'] != 'N/A' else u['email'][:26]
                print(f"{u['id']:<8} | {ident:<28} | {format_ngn(u['current_balance']):<12} | {format_ngn(u['expected_balance']):<12} | +{format_ngn(u['phantom_balance']):<14}")

        # 5. Financial Reality Check
        print("\n[5/5] THE REALITY OF YOUR BANK ACCOUNT BALANCE")
        print("-" * 75)
        print("Why your bank account is low compared to the ₦879k 'Estimated Profit':")
        print(f"  1. Real cash lost to duplicate fallback deliveries: -{format_ngn(total_real_cash_lost)}")
        print(f"  2. Customer deposits tied up in customer wallets:    -{format_ngn(total_wallet_liability)}")
        print(f"     (This is money users gave you that you had to place into Amigo/SMEPlug)")
        print(f"  3. Payment gateway fees (Monnify 1.0% - 1.5%):      ~-{format_ngn(total_deposits * Decimal('0.012'))}")
        print("  4. Working capital sitting in upstream API wallets (Amigo, SMEPlug, etc.)")
        print("=" * 75)

    except Exception as e:
        print(f"\n[ERROR] Audit encountered an error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    run_audit()
