"""Add agent_price to DataPlan

Revision ID: 29ab4217df7f
Revises: 0009_add_perf_indexes
Create Date: 2026-05-24 12:11:48.509339

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '29ab4217df7f'
down_revision: Union[str, None] = '0009_add_perf_indexes'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('data_plans', sa.Column('agent_price', sa.Numeric(precision=12, scale=2), nullable=True))


def downgrade() -> None:
    op.drop_column('data_plans', 'agent_price')
