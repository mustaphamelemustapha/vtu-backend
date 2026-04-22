#!/usr/bin/env python3
"""One-time maintenance script to clear legacy transaction PIN lock state."""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import text

from app.core.database import SessionLocal


def main() -> int:
    parser = argparse.ArgumentParser(description="Clear stale PIN lock fields for all users.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the number of rows that would be updated without changing the database.",
    )
    args = parser.parse_args()

    session = SessionLocal()
    try:
        if args.dry_run:
            count = session.execute(
                text(
                    "SELECT COUNT(*) FROM users WHERE COALESCE(pin_failed_attempts, 0) <> 0 OR pin_locked_until IS NOT NULL"
                )
            ).scalar_one()
            print(f"Would clear stale PIN lock state for {count} user(s).")
            return 0

        result = session.execute(
            text(
                "UPDATE users SET pin_failed_attempts = 0, pin_locked_until = NULL "
                "WHERE COALESCE(pin_failed_attempts, 0) <> 0 OR pin_locked_until IS NOT NULL"
            )
        )
        session.commit()
        print(f"Cleared stale PIN lock state for {result.rowcount or 0} user(s).")
        return 0
    except Exception as exc:
        session.rollback()
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
