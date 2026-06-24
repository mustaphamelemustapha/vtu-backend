"""add_unique_constraint_to_agent_rewards

Revision ID: aa3685e1a68f
Revises: 0014_campaign_is_agent_only
Create Date: 2026-06-24 20:45:02.272100

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'aa3685e1a68f'
down_revision: Union[str, None] = '0014_campaign_is_agent_only'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Deduplicate existing agent_rewards records by keeping only the earliest one per agent & campaign
    op.execute("""
        DELETE FROM agent_rewards
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM agent_rewards
            GROUP BY agent_id, campaign_id
        )
    """)
    with op.batch_alter_table('agent_rewards') as batch_op:
        batch_op.create_unique_constraint('uq_agent_campaign', ['agent_id', 'campaign_id'])


def downgrade() -> None:
    with op.batch_alter_table('agent_rewards') as batch_op:
        batch_op.drop_constraint('uq_agent_campaign', type_='unique')
