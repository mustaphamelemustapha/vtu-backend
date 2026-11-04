import os
import sys
import glob
import shutil
import logging

# 1. Resolve site-packages from the running uvicorn/alembic environment
for cmd in ("uvicorn", "alembic", "fastapi", "gunicorn"):
    cmd_path = shutil.which(cmd)
    if cmd_path:
        prefix = os.path.dirname(os.path.dirname(os.path.realpath(cmd_path)))
        for sp in glob.glob(f"{prefix}/lib/python*/site-packages"):
            if sp not in sys.path:
                sys.path.insert(0, sp)

# 2. Check standard container virtualenv locations
for pattern in (
    "/opt/venv/lib/python*/site-packages",
    "/app/.venv/lib/python*/site-packages",
    "/app/venv/lib/python*/site-packages",
    "/root/.local/lib/python*/site-packages",
    "/usr/local/lib/python*/site-packages",
    "/root/.nix-profile/lib/python*/site-packages",
):
    for sp in glob.glob(pattern):
        if sp not in sys.path:
            sys.path.insert(0, sp)

# 3. Add the project root to the python path so we can import 'app'
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

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
