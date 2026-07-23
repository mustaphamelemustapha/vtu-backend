import sys
import os
sys.path.insert(0, os.path.abspath("."))
from app.core.database import SessionLocal
from app.models.data_plan import DataPlan

db = SessionLocal()
plans = db.query(DataPlan).filter(DataPlan.network == "mtn", DataPlan.data_size.like("%1GB%")).all()
for p in plans:
    print(f"ID:{p.id} Name:{p.plan_name} Price:{p.base_price} Provider:{p.provider} ({p.provider_plan_id}) Fallback:{p.fallback_provider} ({p.fallback_provider_plan_id}) Active:{p.is_active}")
