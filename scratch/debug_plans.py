import sys
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from decimal import Decimal

# Add the app directory to sys.path
sys.path.append("/Users/mustaphamelemustapha/Code/VTU/vtu-backend")

from app.db.session import SessionLocal
from app.models.data_plan import DataPlan
from app.api.v1.endpoints.data import list_data_plans
from app.models.user import User, UserRole

def debug_plans():
    db = SessionLocal()
    try:
        # Mock a user
        mock_user = db.query(User).filter(User.role == UserRole.USER).first()
        if not mock_user:
            print("No user found in DB")
            return

        print(f"Testing list_data_plans for user: {mock_user.email} (Role: {mock_user.role})")
        
        # Check total active plans in DB
        active_count = db.query(DataPlan).filter(DataPlan.is_active == True).count()
        print(f"Total active plans in DB: {active_count}")
        
        # Check Airtel plans
        airtel_active = db.query(DataPlan).filter(DataPlan.network == 'airtel', DataPlan.is_active == True).all()
        print(f"Active Airtel plans in DB: {len(airtel_active)}")
        for p in airtel_active:
            print(f"  - {p.plan_name} (Provider: {p.provider})")

        # Run the actual endpoint logic
        plans = list_data_plans(user=mock_user, db=db)
        print(f"Returned plans from list_data_plans: {len(plans)}")
        
        # Group by network
        networks = {}
        for p in plans:
            networks[p.network] = networks.get(p.network, 0) + 1
        
        print("Breakdown by network:")
        for nw, count in networks.items():
            print(f"  {nw}: {count}")

    finally:
        db.close()

if __name__ == "__main__":
    debug_plans()
