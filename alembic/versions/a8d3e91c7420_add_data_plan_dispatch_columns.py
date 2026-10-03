"""Add dispatch_count and dispatch_plan_id to data_plans

Revision ID: a8d3e91c7420
Revises: 83b9e4a1c5d0
Create Date: 2026-10-03 11:41:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a8d3e91c7420'
down_revision: Union[str, None] = '83b9e4a1c5d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('data_plans', sa.Column('dispatch_count', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('data_plans', sa.Column('dispatch_plan_id', sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column('data_plans', 'dispatch_plan_id')
    op.drop_column('data_plans', 'dispatch_count')
