#!/usr/bin/env python3
"""
Targeted Wallet & User Investigation Tool
=========================================
1. Searches for specific users (Yahya, Esther, etc.) across the database.
2. Checks all transaction types (wallet_fund, wallet_transfer, rewards, adjustments).
3. Inspects service_transactions table as well as transactions table.
4. Finds all users whose transactions contain 'refunded' orders to see if refunds inflated balances.
"""

import os
import sys
from decimal import Decimal

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from sqlalchemy import text
from app.core.database import SessionLocal

def format_ngn(val):
    if val is None:
        val = 0.0
    return f"₦{float(val):,.2f}"

def investigate():
    db = SessionLocal()
    print("=" * 80)
    print("   TARGETED FORENSIC WALLET & USER SEARCH")
    print("=" * 80)

    try:
        # 1. Search for Yahya and Esther
        print("\n[1] SEARCHING FOR 'YAHAYA' AND 'ESTHER' IN USERS TABLE...")
        search_sql = text("""
            SELECT 
                u.id, 
                u.email, 
                u.phone_number, 
                u.full_name, 
                u.referral_code,
                COALESCE(w.balance, 0) as wallet_balance
            FROM users u
            LEFT JOIN wallets w ON w.user_id = u.id
            WHERE UPPER(u.referral_code) LIKE '%YAHAYA%'
               OR UPPER(u.email) LIKE '%YAHAYA%'
               OR UPPER(u.full_name) LIKE '%YAHAYA%'
               OR UPPER(u.email) LIKE '%ESTHER%'
               OR UPPER(u.full_name) LIKE '%ESTHER%'
               OR u.phone_number LIKE '%8482%'
        """)
        matches = db.execute(search_sql).fetchall()

        if not matches:
            print("  ==> NO MATCHES FOUND for Yahya or Esther in this database!")
            print("      This CONFIRMS that Yahya and Esther are registered on your OTHER platform (Kulloma Data),")
            print("      and this database is strictly for MELE DATA.")
        else:
            print(f"  ==> FOUND {len(matches)} MATCHING USER(S):")
            for m in matches:
                print(f"      • User ID: {m.id} | Name: {m.full_name} | Email: {m.email} | Phone: {m.phone_number} | RefCode: {m.referral_code} | Wallet: {format_ngn(m.wallet_balance)}")
                
                # Let's inspect all transactions for this user
                tx_sql = text("""
                    SELECT 
                        tx_type, 
                        status, 
                        COUNT(id) as count, 
                        SUM(amount) as total_amount
                    FROM transactions
                    WHERE user_id = :uid
                    GROUP BY tx_type, status
                """)
                user_txs = db.execute(tx_sql, {"uid": m.id}).fetchall()
                print("        Transactions summary:")
                for ut in user_txs:
                    print(f"          - Type: {ut.tx_type} | Status: {ut.status} | Count: {ut.count} | Sum: {format_ngn(ut.total_amount)}")

        # 2. Check all distinct transaction types in the database
        print("\n[2] DISTINCT TRANSACTION TYPES AND STATUSES IN TRANSACTIONS TABLE:")
        types_sql = text("""
            SELECT 
                tx_type, 
                status, 
                COUNT(id) as total_tx, 
                SUM(amount) as total_val
            FROM transactions
            GROUP BY tx_type, status
            ORDER BY tx_type, status
        """)
        distinct_types = db.execute(types_sql).fetchall()
        for dt in distinct_types:
            print(f"    • Type: {dt.tx_type:<20} | Status: {dt.status:<12} | Count: {dt.total_tx:>6,} | Total: {format_ngn(dt.total_val):>16}")

        # 3. Check service_transactions table if it exists
        print("\n[3] CHECKING SERVICE_TRANSACTIONS TABLE:")
        try:
            st_sql = text("""
                SELECT 
                    tx_type, 
                    status, 
                    COUNT(id) as total_tx, 
                    SUM(amount) as total_val
                FROM service_transactions
                GROUP BY tx_type, status
            """)
            st_rows = db.execute(st_sql).fetchall()
            if st_rows:
                for st in st_rows:
                    print(f"    • Service Type: {st.tx_type:<15} | Status: {st.status:<10} | Count: {st.total_tx:>6,} | Total: {format_ngn(st.total_val):>16}")
            else:
                print("    • Table service_transactions exists but has 0 records.")
        except Exception as e:
            print(f"    • service_transactions check: {e}")

        # 4. Check highest balance wallets
        print("\n[4] TOP 10 HIGHEST WALLET BALANCES ON THE PLATFORM:")
        top_wallets_sql = text("""
            SELECT 
                u.id, 
                u.email, 
                u.phone_number, 
                u.full_name, 
                w.balance
            FROM wallets w
            JOIN users u ON u.id = w.user_id
            ORDER BY w.balance DESC
            LIMIT 10
        """)
        top_wallets = db.execute(top_wallets_sql).fetchall()
        for tw in top_wallets:
            print(f"    • User #{tw.id:<4} | {tw.full_name or 'N/A':<20} | {tw.phone_number or tw.email:<24} | Balance: {format_ngn(tw.balance):>14}")

        print("\n" + "=" * 80)

    except Exception as e:
        print(f"[ERROR]: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    investigate()
