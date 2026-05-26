"""add_promo_and_cashback_columns

Revision ID: 5e2540b1c4e8
Revises: 4e81b4f14959
Create Date: 2026-05-26 14:44:05.401102

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5e2540b1c4e8'
down_revision: Union[str, None] = '4e81b4f14959'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('data_plans', sa.Column('promo_active', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('data_plans', sa.Column('promo_old_price', sa.Numeric(precision=12, scale=2), nullable=True))
    op.add_column('data_plans', sa.Column('promo_label', sa.String(length=255), nullable=True))
    op.add_column('data_plans', sa.Column('cashback_amount', sa.Numeric(precision=12, scale=2), nullable=True))
    op.add_column('data_plans', sa.Column('cashback_label', sa.String(length=255), nullable=True))
    op.create_index('ix_data_plans_promo_active', 'data_plans', ['promo_active'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_data_plans_promo_active', table_name='data_plans')
    op.drop_column('data_plans', 'cashback_label')
    op.drop_column('data_plans', 'cashback_amount')
    op.drop_column('data_plans', 'promo_label')
    op.drop_column('data_plans', 'promo_old_price')
    op.drop_column('data_plans', 'promo_active')

