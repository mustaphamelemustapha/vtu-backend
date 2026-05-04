import sys
import os

# Add the project directory to sys.path
sys.path.append(os.getcwd())

from app.main import _ensure_data_plan_provider_columns, _ensure_transaction_provider_columns
from app.core.database import engine

print("Running data_plans migration...")
_ensure_data_plan_provider_columns()
print("Running transactions migration...")
_ensure_transaction_provider_columns()
print("Migration complete.")
