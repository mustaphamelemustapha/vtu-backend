import os
import sys
import logging

# Add the project root to the python path so we can import 'app'
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal
from app.services.reports import generate_daily_report

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    logger.info("Starting cron job: daily_report")
    db = SessionLocal()
    try:
        results = generate_daily_report(db)
        logger.info(f"Cron job finished. Results: {results}")
    except Exception as e:
        logger.error(f"Error during daily report cron job: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    main()
