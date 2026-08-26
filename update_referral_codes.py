import sys
import os

# Add the project root to the python path so we can import app modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.database import SessionLocal
from app.models.user import User
from app.services.referrals import generate_referral_code

def main():
    db = SessionLocal()
    try:
        users = db.query(User).all()
        updated_count = 0
        for user in users:
            old_code = user.referral_code
            new_code = generate_referral_code(user.full_name)
            # Ensure it's unique
            while db.query(User).filter(User.referral_code == new_code).first():
                new_code = generate_referral_code(user.full_name)
            
            print(f"Updating user {user.id} ({user.full_name}): {old_code} -> {new_code}")
            user.referral_code = new_code
            updated_count += 1
                
        db.commit()
        print(f"\nSuccessfully updated {updated_count} users' referral codes to the new format.")
    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    main()
