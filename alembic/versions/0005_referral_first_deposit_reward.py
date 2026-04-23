"""add first deposit referral reward fields

Revision ID: 0005_referral_first_deposit_reward
Revises: 0004_referrals
Create Date: 2026-04-23
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_referral_first_deposit_reward"
down_revision = "0004_referrals"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("referrals", sa.Column("first_deposit_amount", sa.Numeric(12, 2), nullable=True))


def downgrade():
    op.drop_column("referrals", "first_deposit_amount")
