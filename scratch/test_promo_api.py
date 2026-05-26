import sys
import os

# Add the project directory to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal
from app.models import User, DataPlan
from app.api.v1.endpoints.data import list_data_plans

def test_api():
    db = SessionLocal()
    try:
        # Get a user (e.g. first user in DB)
        user = db.query(User).first()
        if not user:
            print("No users in DB to test API.")
            return

        print(f"Testing list_data_plans API for user: {user.email}")
        plans = list_data_plans(user=user, db=db)
        
        print(f"API returned {len(plans)} plans:")
        for p in plans:
            print(f"- Code: {p.plan_code}")
            print(f"  Name: {p.plan_name}")
            print(f"  Price: {p.price}")
            print(f"  Promo Active: {p.promo_active}")
            print(f"  Promo Old Price: {p.promo_old_price}")
            print(f"  Promo Label: {p.promo_label}")
            print(f"  Cashback Label: {p.cashback_label}")
    finally:
        db.close()

if __name__ == "__main__":
    test_api()
