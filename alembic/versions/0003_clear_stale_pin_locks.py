"""clear stale transaction pin lock state

Revision ID: 0003_clear_stale_pin_locks
Revises: 0002_transaction_pin
Create Date: 2026-04-22
"""
from alembic import op

revision = "0003_clear_stale_pin_locks"
down_revision = "0002_transaction_pin"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE users SET pin_failed_attempts = 0, pin_locked_until = NULL")


def downgrade():
    # No-op: clearing stale lock state is safe and intentionally one-way.
    pass
