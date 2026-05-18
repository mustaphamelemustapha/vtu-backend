import sys
import os

# Add the app directory to path
sys.path.append('/Users/mustaphamelemustapha/Code/VTU/vtu-backend')

from app.core.database import SessionLocal
from app.models import User
from app.models.virtual_account import VirtualAccount, VirtualAccountProvider

def reset_user_accounts(email: str):
    email = email.strip().lower()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            print(f"Error: User with email '{email}' not found.")
            return False
            
        print(f"Found User: {user.full_name} (ID: {user.id})")
        
        # Count existing Monnify accounts
        accounts = db.query(VirtualAccount).filter(
            VirtualAccount.user_id == user.id,
            VirtualAccount.provider == VirtualAccountProvider.MONNIFY
        ).all()
        
        if not accounts:
            print(f"No cached Monnify virtual accounts found for {email}.")
            return True
            
        print(f"Deleting {len(accounts)} cached Monnify virtual account(s) for {email}...")
        
        db.query(VirtualAccount).filter(
            VirtualAccount.user_id == user.id,
            VirtualAccount.provider == VirtualAccountProvider.MONNIFY
        ).delete()
        
        db.commit()
        print(f"Successfully deleted cached Monnify virtual accounts for {email}!")
        print("The next time this user views their wallet, the app will prompt them to generate new, fresh virtual accounts.")
        return True
        
    except Exception as e:
        db.rollback()
        print(f"Database error: {e}")
        return False
    finally:
        db.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python reset_accounts.py <user_email>")
        sys.exit(1)
        
    email_arg = sys.argv[1]
    reset_user_accounts(email_arg)
