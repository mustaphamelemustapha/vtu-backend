import sys, os, json
sys.path.append(os.getcwd())
from app.core.database import SessionLocal
from app.models import Transaction, WalletLedger

db = SessionLocal()
txs = db.query(Transaction).filter(Transaction.reference.like("ADMIN_ADJUST_%")).order_by(Transaction.id.desc()).limit(5).all()
for tx in txs:
    print(f"TX: {tx.reference} user={tx.user_id} amt={tx.amount} type={tx.tx_type} status={tx.status} fail={tx.failure_reason}")
    ledger = db.query(WalletLedger).filter(WalletLedger.reference == tx.reference).first()
    if ledger:
        print(f"  LEDGER FOUND: user={ledger.wallet.user_id} desc={ledger.description}")
    else:
        print("  LEDGER NOT FOUND!")
