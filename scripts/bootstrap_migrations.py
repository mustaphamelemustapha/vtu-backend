from __future__ import annotations

import os
import subprocess
import sys

from sqlalchemy import create_engine, text


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required for migrations", file=sys.stderr)
        return 1

    import time
    from sqlalchemy.exc import OperationalError
    
    engine = create_engine(database_url)
    
    max_retries = 10
    retry_delay = 3
    connection = None
    
    for attempt in range(max_retries):
        try:
            connection = engine.connect()
            break
        except OperationalError as e:
            print(f"Database connection attempt {attempt + 1}/{max_retries} failed: {e}")
            if attempt < max_retries - 1:
                print(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
            else:
                print("Max connection retries reached. Failing migration.", file=sys.stderr)
                return 1

    with connection:
        alembic_version_exists = connection.execute(
            text("SELECT to_regclass('public.alembic_version')")
        ).scalar()
        if not alembic_version_exists:
            users_exists = connection.execute(
                text("SELECT to_regclass('public.users')")
            ).scalar()
            if users_exists:
                print("Stamping existing database to 0003_clear_stale_pin_locks")
                subprocess.run(
                    ["alembic", "stamp", "0003_clear_stale_pin_locks"],
                    check=True,
                    env=os.environ.copy(),
                )

    print("Running Alembic upgrade")
    subprocess.run(["alembic", "upgrade", "head"], check=True, env=os.environ.copy())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
