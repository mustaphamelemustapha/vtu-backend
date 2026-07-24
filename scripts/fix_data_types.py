import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal
from app.models import DataPlan

def fix_data_types():
    db = SessionLocal()
    try:
        plans = db.query(DataPlan).all()
        updated = 0
        for plan in plans:
            if plan.data_type:
                continue

            name_lower = (plan.plan_name or "").lower()
            inferred_type = None
            if "sme" in name_lower:
                inferred_type = "SME"
            elif "cg" in name_lower or "c.g" in name_lower or "corporate" in name_lower or "cooperate" in name_lower:
                inferred_type = "CG"
            elif "gifting" in name_lower or "direct" in name_lower:
                inferred_type = "Gifting"

            # Give it a fallback so it doesn't remain empty
            if not inferred_type:
                inferred_type = "Gifting" if "9mobile" in (plan.network or "").lower() or "glo" in (plan.network or "").lower() else "SME"
            
            plan.data_type = inferred_type
            updated += 1
        
        db.commit()
        print(f"Successfully updated {updated} plans with an inferred data_type.")
    except Exception as e:
        print(f"Error updating plans: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    fix_data_types()
