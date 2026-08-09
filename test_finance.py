import asyncio
import logging
from app.core.database import SessionLocal
from app.api.v1.endpoints.finance import get_finance_overview

logging.basicConfig(level=logging.INFO)

def test_finance():
    db = SessionLocal()
    try:
        # Mock admin user
        class DummyUser:
            id = 1
        
        result = get_finance_overview(db, DummyUser())
        print("SUCCESS!")
        print(result)
    except Exception as e:
        logging.exception("FAILED with error")
    finally:
        db.close()

if __name__ == "__main__":
    test_finance()
