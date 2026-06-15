import os
import sys
from datetime import datetime, timezone

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.database import SessionLocal
from app.models.agent import RewardCampaign
from app.models.transaction import Transaction

db = SessionLocal()
try:
    camp = db.query(RewardCampaign).first()
    tx = db.query(Transaction).filter(Transaction.user_id == 3).first()
    
    if camp and tx:
        print(f"Campaign activated_at: {camp.activated_at} | tzinfo: {camp.activated_at.tzinfo if camp.activated_at else None}")
        print(f"Transaction created_at: {tx.created_at} | tzinfo: {tx.created_at.tzinfo if tx.created_at else None}")
        
        # Test the comparison logic
        camp_time = camp.activated_at or camp.created_at
        if camp_time and camp_time.tzinfo is not None:
            camp_time = camp_time.astimezone(timezone.utc).replace(tzinfo=None)
            
        tx_time = tx.created_at
        if tx_time and tx_time.tzinfo is not None:
            tx_time = tx_time.astimezone(timezone.utc).replace(tzinfo=None)
            
        print(f"Normalized camp_time: {camp_time} | normalized tx_time: {tx_time}")
        print(f"Is tx_time >= camp_time? {tx_time >= camp_time if tx_time and camp_time else 'N/A'}")
    else:
        print("Campaign or Transaction not found.")
finally:
    db.close()
