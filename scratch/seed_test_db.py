import sys
import os
from datetime import datetime, timezone, timedelta
from decimal import Decimal

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session
from sqlalchemy import event
from app.core.database import SessionLocal, Base, engine
from app.models import User, Transaction, TransactionStatus, TransactionType, ServiceTransaction, DataPlan, UserRole
from app.core.security import hash_password

@event.listens_for(engine, "connect")
def register_sqlite_functions(dbapi_connection, connection_record):
    import sqlite3
    if isinstance(dbapi_connection, sqlite3.Connection):
        # sqlite3 doesn't have a built-in now() function like postgresql
        dbapi_connection.create_function("now", 0, lambda: datetime.now(timezone.utc).isoformat())

# First, recreate/ensure tables
Base.metadata.create_all(bind=engine)

db = SessionLocal()
try:
    # Clear existing data
    db.query(ServiceTransaction).delete()
    db.query(Transaction).delete()
    db.query(DataPlan).delete()
    db.query(User).delete()
    db.commit()
    
    print("Cleaned database tables.")
    
    # 1. Seed Users (total = 10, active = 8, inactive = 2)
    users = []
    for i in range(1, 11):
        is_active = i <= 8
        is_verified = i <= 6
        email = f"user{i}@example.com"
        user = User(
            email=email,
            full_name=f"User {i}",
            hashed_password=hash_password("password123"),
            role=UserRole.USER,
            is_active=is_active,
            is_verified=is_verified,
            referral_code=f"REF{i:03d}"
        )
        db.add(user)
        users.append(user)
    db.commit()
    # Refresh to get IDs
    for u in users:
        db.refresh(u)
    print(f"Seeded {len(users)} users (8 active, 2 inactive).")
    
    # 2. Seed Data Plans
    plans = [
        DataPlan(plan_code="mtn_1gb", base_price=Decimal("150.00"), plan_name="MTN 1GB", data_size="1GB", validity="30 Days", network="mtn"),
        DataPlan(plan_code="glo_2gb", base_price=Decimal("280.00"), plan_name="GLO 2GB", data_size="2GB", validity="30 Days", network="glo"),
    ]
    for p in plans:
        db.add(p)
    db.commit()
    print("Seeded data plans.")
    
    # 3. Seed Transactions (in the past and today)
    now = datetime.now(timezone.utc)
    one_day_ago = now - timedelta(days=1)
    two_days_ago = now - timedelta(days=2)
    
    txs = [
        # Data transaction with matching plan (revenue = 200, cost = 150, profit = 50)
        Transaction(
            user_id=users[0].id,
            reference="TX001",
            network="mtn",
            data_plan_code="mtn_1gb",
            amount=Decimal("200.00"),
            status=TransactionStatus.SUCCESS,
            tx_type=TransactionType.DATA,
            created_at=two_days_ago
        ),
        # Data transaction with non-matching plan (cost estimate should fallback to amount)
        Transaction(
            user_id=users[0].id,
            reference="TX002",
            network="airtel",
            data_plan_code="airtel_unknown",
            amount=Decimal("300.00"),
            status=TransactionStatus.SUCCESS,
            tx_type=TransactionType.DATA,
            created_at=one_day_ago
        ),
        # Wallet fund transaction (should NOT be counted as revenue)
        Transaction(
            user_id=users[1].id,
            reference="TX003",
            amount=Decimal("1000.00"),
            status=TransactionStatus.SUCCESS,
            tx_type=TransactionType.WALLET_FUND,
            created_at=one_day_ago
        ),
        # Today's successful data transaction (revenue = 250, cost = 150, profit = 100)
        Transaction(
            user_id=users[2].id,
            reference="TX004",
            network="mtn",
            data_plan_code="mtn_1gb",
            amount=Decimal("250.00"),
            status=TransactionStatus.SUCCESS,
            tx_type=TransactionType.DATA,
            created_at=now
        ),
        # Today's failed data transaction
        Transaction(
            user_id=users[3].id,
            reference="TX005",
            network="glo",
            data_plan_code="glo_2gb",
            amount=Decimal("350.00"),
            status=TransactionStatus.FAILED,
            tx_type=TransactionType.DATA,
            created_at=now
        ),
        # Today's pending data transaction
        Transaction(
            user_id=users[4].id,
            reference="TX006",
            network="glo",
            data_plan_code="glo_2gb",
            amount=Decimal("350.00"),
            status=TransactionStatus.PENDING,
            tx_type=TransactionType.DATA,
            created_at=now
        ),
    ]
    for tx in txs:
        db.add(tx)
    db.commit()
    print(f"Seeded {len(txs)} transactions.")
    
    # 4. Seed Service Transactions
    sts = [
        # Past successful service transaction (amount = 500, base_amount = 450, profit = 50)
        ServiceTransaction(
            user_id=users[0].id,
            reference="ST001",
            tx_type="electricity",
            amount=Decimal("500.00"),
            status="success",
            provider="ikeja",
            meta={"base_amount": "450.00"},
            created_at=two_days_ago
        ),
        # Today's successful service transaction (amount = 1000, no base_amount in meta => profit = 0)
        ServiceTransaction(
            user_id=users[1].id,
            reference="ST002",
            tx_type="cable",
            amount=Decimal("1000.00"),
            status="success",
            provider="dstv",
            meta={},
            created_at=now
        ),
        # Today's failed service transaction
        ServiceTransaction(
            user_id=users[2].id,
            reference="ST003",
            tx_type="airtime",
            amount=Decimal("100.00"),
            status="failed",
            provider="mtn",
            created_at=now
        )
    ]
    for st in sts:
        db.add(st)
    db.commit()
    print(f"Seeded {len(sts)} service transactions.")
    
finally:
    db.close()
