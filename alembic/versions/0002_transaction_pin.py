"""transaction pin security

Revision ID: 0002_transaction_pin
Revises: 0001_initial
Create Date: 2026-04-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_transaction_pin"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("pin_hash", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("pin_set_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "users",
        sa.Column("pin_failed_attempts", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column("users", sa.Column("pin_locked_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "users",
        sa.Column("pin_reset_token_hash", sa.String(255), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("pin_reset_token_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_users_pin_reset_token_hash",
        "users",
        ["pin_reset_token_hash"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_users_pin_reset_token_hash", table_name="users")
    op.drop_column("users", "pin_reset_token_expires_at")
    op.drop_column("users", "pin_reset_token_hash")
    op.drop_column("users", "pin_locked_until")
    op.drop_column("users", "pin_failed_attempts")
    op.drop_column("users", "pin_set_at")
    op.drop_column("users", "pin_hash")
