#!/usr/bin/env python3
import sys
import logging
from app.core.database import SessionLocal
from app.services.commissions import reconcile_missing_welcome_bonuses

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("reconcile_kulloma")

def main():
    db = SessionLocal()
    try:
        logger.info("Starting Kulloma Data 2GB Welcome Bonus reconciliation...")
        awarded = reconcile_missing_welcome_bonuses(db, brand="kullomadata", limit=2000)
        logger.info("Successfully reconciled %d users with ₦300 welcome gift!", len(awarded))
        for item in awarded:
            logger.info("  ✓ User #%s (%s): Credited ₦%s [Ref: %s]", item['user_id'], item['email'], item['amount'], item['reference'])
    finally:
        db.close()

if __name__ == "__main__":
    main()
