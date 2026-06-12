import os
import sys

# Add backend root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal
from app.models.service_transaction import ServiceTransaction

db = SessionLocal()
try:
    print("--- Service Transactions (Electricity) ---")
    txs = db.query(ServiceTransaction).filter(ServiceTransaction.tx_type == "electricity").order_by(ServiceTransaction.id.desc()).all()
    for t in txs:
        print(f"ID: {t.id}, Ref: {t.reference}, Status: {t.status}, Customer: {t.customer}, Amount: {t.amount}")
        print(f"Meta: {t.meta}")
        print("-" * 50)
finally:
    db.close()
