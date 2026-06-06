"""add activated_at to reward_campaigns

Revision ID: 0010_campaign_activated_at
Revises: 71dfc54e9fcd
Create Date: 2026-06-06 11:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0010_campaign_activated_at'
down_revision: Union[str, None] = '71dfc54e9fcd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('reward_campaigns', sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('reward_campaigns', 'activated_at')
