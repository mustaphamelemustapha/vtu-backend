from app.core.database import SessionLocal
from app.models import Transaction, Wallet, User

db = SessionLocal()
try:
    # Check if transaction with external_reference R-XOLNQDQJKP exists
    tx = db.query(Transaction).filter(Transaction.external_reference == "R-XOLNQDQJKP").first()
    print("TX WITH EXT REF:", tx)
    if tx:
        print("  id:", tx.id)
        print("  status:", tx.status)
        print("  amount:", tx.amount)
        print("  ref:", tx.reference)

    # Check user 8 wallet balance
    user = db.query(User).filter(User.id == 8).first()
    if user:
        wallet = user.wallet
        print("USER 8 WALLET:")
        print("  balance:", wallet.balance if wallet else "No wallet")
    else:
        print("User 8 not found")
finally:
    db.close()
