"""add agent_upgrade_seen to users

Revision ID: 0011_user_agent_upgrade_seen
Revises: 0010_campaign_activated_at
Create Date: 2026-06-06 11:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0011_user_agent_upgrade_seen'
down_revision: Union[str, None] = '0010_campaign_activated_at'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('agent_upgrade_seen', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column('users', 'agent_upgrade_seen')
