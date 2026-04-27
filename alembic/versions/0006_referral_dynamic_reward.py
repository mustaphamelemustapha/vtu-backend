"""set referral reward default to dynamic first-deposit computation

Revision ID: 0006_referral_dynamic_reward
Revises: 0005_referral_first_deposit
Create Date: 2026-04-27
"""

from alembic import op


revision = "0006_referral_dynamic_reward"
down_revision = "0005_referral_first_deposit"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE referrals ALTER COLUMN reward_amount SET DEFAULT 0")
    op.execute(
        """
        UPDATE referrals
        SET reward_amount = 0
        WHERE status = 'pending'
          AND (first_deposit_amount IS NULL OR first_deposit_amount = 0)
        """
    )


def downgrade():
    op.execute("ALTER TABLE referrals ALTER COLUMN reward_amount SET DEFAULT 2000")
    op.execute(
        """
        UPDATE referrals
        SET reward_amount = 2000
        WHERE status = 'pending'
          AND (first_deposit_amount IS NULL OR first_deposit_amount = 0)
        """
    )
