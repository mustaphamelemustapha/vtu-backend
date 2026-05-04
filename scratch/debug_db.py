import os
import sys
import sqlite3
from urllib.parse import urlparse

# Add the app directory to path
sys.path.append("/Users/mustaphamelemustapha/Code/VTU/vtu-backend")

try:
    from app.core.config import get_settings
    settings = get_settings()
    db_url = str(settings.database_url)
    print(f"Using Database URL: {db_url}")

    if db_url.startswith("sqlite"):
        path = db_url.replace("sqlite:///", "")
        if not path.startswith("/"):
            path = "/Users/mustaphamelemustapha/Code/VTU/vtu-backend/" + path
        print(f"Connecting to SQLite: {path}")
        conn = sqlite3.connect(path)
        cursor = conn.cursor()
        cursor.execute("SELECT network, is_active, count(*) FROM data_plans GROUP BY network, is_active;")
        rows = cursor.fetchall()
        print("Data Plans Summary:")
        if not rows:
            print("No plans found in database.")
        for row in rows:
            print(f"Network: {row[0]}, Active: {row[1]}, Count: {row[2]}")
        conn.close()
    elif db_url.startswith("postgresql"):
        print("PostgreSQL detected.")
    else:
        print(f"Unknown DB scheme: {db_url}")

except Exception as e:
    print(f"Error: {e}")
