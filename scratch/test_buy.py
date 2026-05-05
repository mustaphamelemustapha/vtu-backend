import sys
import traceback
from decimal import Decimal
from fastapi import Request

sys.path.append("/Users/mustaphamelemustapha/Code/VTU/vtu-backend")

import os
# Hack config to not load
os.environ["DATABASE_URL"] = "postgresql://user:pass@localhost/sqlite"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Bypass database.py so we use our own engine
import app.core.database
app.core.database.engine = create_engine("sqlite:///./test.db", connect_args={"check_same_thread": False})
app.core.database.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=app.core.database.engine)
db = app.core.database.SessionLocal()

from app.models.user import User
from app.schemas.data import BuyDataRequest
from app.api.v1.endpoints.data import buy_data

try:
    user = db.query(User).filter(User.email == "mmtechglobe@gmail.com").first()
    if not user:
        print("User not found.")
        sys.exit(1)

    from app.services.wallet import get_or_create_wallet
    wallet = get_or_create_wallet(db, user.id)
    wallet.balance = Decimal("10000.00")
    db.commit()

    from app.models.data_plan import DataPlan
    plan = db.query(DataPlan).filter(DataPlan.is_active == True).first()
    if not plan:
        print("No active plans!")
        sys.exit(1)

    req = BuyDataRequest(
        client_request_id="test-123",
        plan_code=plan.plan_code,
        phone_number="08012345678",
        network=plan.network,
        ported_number=True
    )
    
    mock_request = Request({
        "type": "http", 
        "method": "POST", 
        "headers": [],
        "client": ("127.0.0.1", 8000),
        "app": {}
    })
    
    try:
        res = buy_data(mock_request, req, user, db)
        print("Result:", res)
    except Exception as e:
        print("EXCEPTION CAUGHT:")
        traceback.print_exc()

finally:
    db.close()
