import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.models.data_plan import DataPlan

def clean_old_data_plans():
    db = SessionLocal()
    try:
        plans = db.query(DataPlan).all()
        deleted = 0
        for plan in plans:
            # Old format: network:id (1 colon). New format: provider:network:id (2 colons)
            code = str(plan.plan_code or "")
            colons = code.count(":")
            provider = str(plan.provider or "").strip()
            
            # Delete if it's the old format (less than 2 colons) OR if provider is missing
            if colons < 2 or not provider or provider.lower() == "unknown":
                db.delete(plan)
                deleted += 1
        
        db.commit()
        print(f"Successfully deleted {deleted} old/unknown data plans!")
    except Exception as e:
        db.rollback()
        print(f"Error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    clean_old_data_plans()
