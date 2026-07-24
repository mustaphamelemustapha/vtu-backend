import os
import sys
import logging

# Add the project root to the python path so we can import 'app'
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal
from app.services.monitoring import check_provider_balances

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    logger.info("Starting cron job: check_provider_balances")
    db = SessionLocal()
    try:
        results = check_provider_balances(db)
        logger.info(f"Cron job finished. Results: {results}")
    except Exception as e:
        logger.error(f"Error during cron job: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    main()
