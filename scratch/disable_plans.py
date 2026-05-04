import sys
import os
from dotenv import load_dotenv

# Load the production environment variables from .env
load_dotenv("/Users/mustaphamelemustapha/Code/VTU/vtu-backend/.env")

sys.path.append("/Users/mustaphamelemustapha/Code/VTU/vtu-backend")

from app.core.database import SessionLocal
from app.models.data_plan import DataPlan

def disable_all():
    db = SessionLocal()
    try:
        count = db.query(DataPlan).update({DataPlan.is_active: False})
        db.commit()
        print(f"Success! Disabled {count} data plans.")
    except Exception as e:
        db.rollback()
        print(f"Error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    disable_all()
