import sys
import os
sys.path.insert(0, os.path.abspath("."))
from app.core.database import SessionLocal
from app.models.data_plan import DataPlan

db = SessionLocal()
plans = db.query(DataPlan).filter(DataPlan.is_active == True).all()
print(f"Total active plans: {len(plans)}")
for p in plans[:5]:
    print(f"[{p.network.upper()}] {p.plan_name} - {p.data_size} - Provider:{p.provider} ({p.provider_plan_id}) Fallback:{p.fallback_provider} ({p.fallback_provider_plan_id})")
