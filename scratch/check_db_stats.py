import sys
import os
from datetime import datetime, timezone, timedelta

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.models import User, Transaction, TransactionStatus, TransactionType, ServiceTransaction
from sqlalchemy import func, inspect

db = SessionLocal()
try:
    total_users = db.query(func.count(User.id)).scalar() or 0
    active_users_field = db.query(func.count(User.id)).filter(User.is_active == True).scalar() or 0
    verified_users = db.query(func.count(User.id)).filter(User.is_verified == True).scalar() or 0
    
    # Active based on recent transactions (e.g., in last 30 days)
    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    active_users_30d = db.query(func.count(func.distinct(Transaction.user_id))).filter(
        Transaction.status == TransactionStatus.SUCCESS,
        Transaction.created_at >= thirty_days_ago
    ).scalar() or 0
    
    total_tx = db.query(func.count(Transaction.id)).scalar() or 0
    success_tx = db.query(func.count(Transaction.id)).filter(Transaction.status == TransactionStatus.SUCCESS).scalar() or 0
    failed_tx = db.query(func.count(Transaction.id)).filter(Transaction.status == TransactionStatus.FAILED).scalar() or 0
    pending_tx = db.query(func.count(Transaction.id)).filter(Transaction.status == TransactionStatus.PENDING).scalar() or 0
    
    # Today's transaction count
    now_utc = datetime.now(timezone.utc)
    day_start_utc = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    today_tx_all = db.query(func.count(Transaction.id)).filter(Transaction.created_at >= day_start_utc).scalar() or 0
    today_tx_success = db.query(func.count(Transaction.id)).filter(
        Transaction.status == TransactionStatus.SUCCESS,
        Transaction.created_at >= day_start_utc
    ).scalar() or 0
    
    print(f"Total Users: {total_users}")
    print(f"Active Users (is_active == True): {active_users_field}")
    print(f"Verified Users: {verified_users}")
    print(f"Active Users (30d tx): {active_users_30d}")
    print(f"Total Transactions: {total_tx}")
    print(f"Success Transactions: {success_tx}")
    print(f"Failed Transactions: {failed_tx}")
    print(f"Pending Transactions: {pending_tx}")
    print(f"Today's Transactions (All): {today_tx_all}")
    print(f"Today's Transactions (Success): {today_tx_success}")
    
    print("\n--- Transactions by Type & Status ---")
    tx_breakdown = db.query(
        Transaction.tx_type, 
        Transaction.status, 
        func.count(Transaction.id), 
        func.sum(Transaction.amount)
    ).group_by(Transaction.tx_type, Transaction.status).all()
    for tx_type, status, count, amount_sum in tx_breakdown:
        print(f"Type: {tx_type}, Status: {status}, Count: {count}, Sum: {amount_sum}")
        
    # Check service transactions
    if inspect(db.bind).has_table("service_transactions"):
        total_st = db.query(func.count(ServiceTransaction.id)).scalar() or 0
        success_st = db.query(func.count(ServiceTransaction.id)).filter(ServiceTransaction.status == TransactionStatus.SUCCESS.value).scalar() or 0
        today_st_all = db.query(func.count(ServiceTransaction.id)).filter(ServiceTransaction.created_at >= day_start_utc).scalar() or 0
        print(f"\nTotal Service Transactions: {total_st}")
        print(f"Success Service Transactions: {success_st}")
        print(f"Today's Service Transactions: {today_st_all}")
        
        print("\n--- Service Transactions by Type & Status ---")
        st_breakdown = db.query(
            ServiceTransaction.tx_type,
            ServiceTransaction.status,
            func.count(ServiceTransaction.id),
            func.sum(ServiceTransaction.amount)
        ).group_by(ServiceTransaction.tx_type, ServiceTransaction.status).all()
        for tx_type, status, count, amount_sum in st_breakdown:
            print(f"Type: {tx_type}, Status: {status}, Count: {count}, Sum: {amount_sum}")
        
finally:
    db.close()
