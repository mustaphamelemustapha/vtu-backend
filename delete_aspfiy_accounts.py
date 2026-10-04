import sys
import os

# Add app directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.core.database import SessionLocal
from app.models.virtual_account import VirtualAccount, VirtualAccountProvider

def run():
    db = SessionLocal()
    try:
        deleted = db.query(VirtualAccount).filter(VirtualAccount.provider == VirtualAccountProvider.ASPFIY).delete()
        db.commit()
        print(f"Deleted {deleted} ASPFIY accounts.")
    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == '__main__':
    run()
