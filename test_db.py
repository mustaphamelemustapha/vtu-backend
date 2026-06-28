import sys
import asyncio
from datetime import date
from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from app.api.v1.endpoints.admin import _as_utc_start, _as_utc_end
from app.models import Transaction, User

print("UTC start for 2026-06-28:", _as_utc_start(date(2026, 6, 28)))
print("UTC end for 2026-06-28:", _as_utc_end(date(2026, 6, 28)))
