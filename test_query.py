import asyncio
from sqlalchemy import create_engine, select, or_, func, cast, String
from sqlalchemy.orm import sessionmaker
from app.models import Transaction, User, ServiceTransaction
from app.api.v1.endpoints.admin import _as_utc_start, _as_utc_end
import datetime

def test():
    print("Testing...")

test()
