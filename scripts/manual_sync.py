import os
import sys
import logging

# Add the app directory to path
sys.path.append(os.path.join(os.getcwd()))

from app.core.database import SessionLocal
from app.api.v1.endpoints.data import _upsert_plan_from_provider, _parse_size_gb
from app.providers.smeplug_provider import SMEPlugProvider
from app.services.amigo import AmigoClient
from app.services.bills import get_bills_provider
from app.models import DataPlan

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("manual_sync")

def run_sync():
    db = SessionLocal()
    try:
        # 1. Airtel (SMEPlug)
        logger.info("Syncing Airtel from SMEPlug...")
        try:
            sme = SMEPlugProvider()
            items = sme.get_all_plans() # Use get_all_plans to see everything
            logger.info(f"Found {len(items)} total plans from SMEPlug")
            touched = 0
            for item in items:
                if _upsert_plan_from_provider(db, item):
                    touched += 1
            db.commit()
            logger.info(f"SMEPlug sync done. Touched {touched} plans.")
        except Exception as e:
            db.rollback()
            logger.error(f"SMEPlug sync failed: {e}")

        # 2. MTN/Glo (Amigo)
        logger.info("Syncing MTN/Glo from Amigo...")
        try:
            amigo = AmigoClient()
            res = amigo.fetch_data_plans()
            items = res.get("data", [])
            logger.info(f"Found {len(items)} plans from Amigo")
            touched = 0
            for item in items:
                if _upsert_plan_from_provider(db, item):
                    touched += 1
            db.commit()
            logger.info(f"Amigo sync done. Touched {touched} plans.")
        except Exception as e:
            db.rollback()
            logger.error(f"Amigo sync failed: {e}")

        # 3. 9mobile
        logger.info("Syncing 9mobile...")
        try:
            provider = get_bills_provider()
            if hasattr(provider, "fetch_data_variations"):
                items = provider.fetch_data_variations("9mobile")
                logger.info(f"Found {len(items)} plans for 9mobile")
                touched = 0
                for item in items:
                    item["network"] = "9mobile"
                    item["provider"] = "clubkonnect"
                    if _upsert_plan_from_provider(db, item):
                        touched += 1
                db.commit()
                logger.info(f"9mobile sync done. Touched {touched} plans.")
        except Exception as e:
            db.rollback()
            logger.error(f"9mobile sync failed: {e}")

        # 4. Final Verification
        active_count = db.query(DataPlan).filter(DataPlan.is_active == True).count()
        total_count = db.query(DataPlan).count()
        logger.info(f"Sync complete. Active plans: {active_count} / {total_count}")
        
        if active_count == 0 and total_count > 0:
            logger.warning("All plans are INACTIVE. Forcing them to active...")
            db.query(DataPlan).update({DataPlan.is_active: True})
            db.commit()
            logger.info("All plans forced to active.")

    finally:
        db.close()

if __name__ == "__main__":
    run_sync()
