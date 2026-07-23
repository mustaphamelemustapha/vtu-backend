"""add data_type to data_plans

Revision ID: d0877ef36021
Revises: 87c4b91769da
Create Date: 2026-07-23 20:47:48.801391

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd0877ef36021'
down_revision: Union[str, None] = '87c4b91769da'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use batch_alter_table for SQLite compatibility
    with op.batch_alter_table('data_plans') as batch_op:
        batch_op.add_column(sa.Column('data_type', sa.String(length=64), nullable=True))
        batch_op.create_index(batch_op.f('ix_data_plans_data_type'), ['data_type'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('data_plans') as batch_op:
        batch_op.drop_index(batch_op.f('ix_data_plans_data_type'))
        batch_op.drop_column('data_type')
