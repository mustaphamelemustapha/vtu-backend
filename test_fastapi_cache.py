import asyncio
from fastapi import FastAPI, Query, Depends
from fastapi.testclient import TestClient
from datetime import date
from functools import wraps
from typing import Optional

app = FastAPI()

def cache_endpoint(ttl_seconds: int = 15):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            print("kwargs in wrapper:", kwargs)
            return func(*args, **kwargs)
        return wrapper
    return decorator

@app.get("/test")
@cache_endpoint()
def test_endpoint(
    from_date: Optional[date] = Query(default=None, alias="from"),
    to_date: Optional[date] = Query(default=None, alias="to")
):
    print("In endpoint:", from_date, to_date)
    return {"from": from_date, "to": to_date}

client = TestClient(app)
response = client.get("/test?from=2026-06-28&to=2026-06-29")
print(response.json())
