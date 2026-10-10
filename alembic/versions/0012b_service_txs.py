"""create service_transactions table

Revision ID: 0012b_create_service_transactions
Revises: 0012_user_bvn_nin_hashes
Create Date: 2026-10-08 14:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0012b_service_txs'
down_revision: Union[str, None] = '0012_user_bvn_nin_hashes'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'service_transactions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('reference', sa.String(length=64), nullable=False),
        sa.Column('tx_type', sa.String(length=32), nullable=False),
        sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('provider', sa.String(length=64), nullable=True),
        sa.Column('customer', sa.String(length=128), nullable=True),
        sa.Column('product_code', sa.String(length=64), nullable=True),
        sa.Column('external_reference', sa.String(length=128), nullable=True),
        sa.Column('failure_reason', sa.String(length=255), nullable=True),
        sa.Column('meta', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_service_transactions_id'), 'service_transactions', ['id'], unique=False)
    op.create_index(op.f('ix_service_transactions_reference'), 'service_transactions', ['reference'], unique=True)
    op.create_index(op.f('ix_service_transactions_user_id'), 'service_transactions', ['user_id'], unique=False)
    op.create_index('ix_service_transactions_user_status', 'service_transactions', ['user_id', 'status'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_service_transactions_user_status', table_name='service_transactions')
    op.drop_index(op.f('ix_service_transactions_user_id'), table_name='service_transactions')
    op.drop_index(op.f('ix_service_transactions_reference'), table_name='service_transactions')
    op.drop_index(op.f('ix_service_transactions_id'), table_name='service_transactions')
    op.drop_table('service_transactions')
