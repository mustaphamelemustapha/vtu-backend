"""add is_agent_only to reward_campaigns

Revision ID: 0014_campaign_is_agent_only
Revises: 0013_add_created_at_indexes
Create Date: 2026-06-15 18:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0014_campaign_is_agent_only'
down_revision: Union[str, None] = '0013_add_created_at_indexes'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c['name'] for c in inspector.get_columns('reward_campaigns')]
    if 'is_agent_only' not in columns:
        op.add_column('reward_campaigns', sa.Column('is_agent_only', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c['name'] for c in inspector.get_columns('reward_campaigns')]
    if 'is_agent_only' in columns:
        op.drop_column('reward_campaigns', 'is_agent_only')
