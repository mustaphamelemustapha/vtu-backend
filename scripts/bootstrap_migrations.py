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

    engine = create_engine(database_url)
    with engine.connect() as connection:
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
