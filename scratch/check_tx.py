from app.core.database import SessionLocal
from app.models.transaction import Transaction, TransactionType

db = SessionLocal()
count_wallet_fund = db.query(Transaction).filter(Transaction.tx_type == TransactionType.WALLET_FUND).count()
print(f"wallet_fund count: {count_wallet_fund}")

count_all = db.query(Transaction).count()
print(f"all tx count: {count_all}")
