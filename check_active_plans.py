import sys
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from decimal import Decimal

# Add the app directory to path
sys.path.append('/Users/mustaphamelemustapha/Code/VTU/vtu-backend')

from app.models import DataPlan
from app.core.database import SessionLocal

db = SessionLocal()
try:
    active_plans = db.query(DataPlan).filter(DataPlan.is_active == True).all()
    print(f"Total Active Plans in DB: {len(active_plans)}")
    
    networks = {}
    for p in active_plans:
        net = str(p.network).lower()
        networks[net] = networks.get(net, 0) + 1
    
    for net, count in networks.items():
        print(f"- {net}: {count} plans")
        
finally:
    db.close()
