import logging
from datetime import datetime, time
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.user import User, UserRole
from app.models.transaction import Transaction, TransactionStatus, TransactionType
from app.services.email import send_admin_daily_report_email

logger = logging.getLogger(__name__)


def generate_daily_report(db: Session, target_date=None):
    if target_date is None:
        target_date = datetime.utcnow().date()
        
    start_time = datetime.combine(target_date, time.min)
    end_time = datetime.combine(target_date, time.max)
    
    logger.info(f"Generating daily report for {target_date} ({start_time} to {end_time})")

    # Total Sales (Successful, excluding Wallet Funding)
    sales = db.query(func.sum(Transaction.amount)).filter(
        Transaction.created_at >= start_time,
        Transaction.created_at <= end_time,
        Transaction.status == TransactionStatus.SUCCESS,
        Transaction.tx_type != TransactionType.WALLET_FUND
    ).scalar() or 0.0

    # Total Wallet Funding (Successful)
    funding = db.query(func.sum(Transaction.amount)).filter(
        Transaction.created_at >= start_time,
        Transaction.created_at <= end_time,
        Transaction.status == TransactionStatus.SUCCESS,
        Transaction.tx_type == TransactionType.WALLET_FUND
    ).scalar() or 0.0

    # New Users
    new_users_count = db.query(func.count(User.id)).filter(
        User.created_at >= start_time,
        User.created_at <= end_time
    ).scalar() or 0

    # Pending Transactions (Current status, regardless of when they were created)
    pending_txs_count = db.query(func.count(Transaction.id)).filter(
        Transaction.status == TransactionStatus.PENDING
    ).scalar() or 0

    stats = {
        "date": target_date.strftime("%B %d, %Y"),
        "total_sales": float(sales),
        "total_funding": float(funding),
        "new_users": new_users_count,
        "pending_txs": pending_txs_count,
    }

    logger.info(f"Daily report stats: {stats}")

    # Send to all admins
    admins = db.query(User).filter(User.role == UserRole.ADMIN).all()
    if not admins:
        logger.warning("No admins found to send daily report to.")
        return stats

    for admin in admins:
        if admin.email:
            try:
                send_admin_daily_report_email(admin.email, stats)
                logger.info(f"Sent daily report to {admin.email}")
            except Exception as e:
                logger.error(f"Failed to send daily report to {admin.email}: {e}")

    return stats
