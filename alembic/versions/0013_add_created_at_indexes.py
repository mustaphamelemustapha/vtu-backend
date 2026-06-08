"""add created_at indexes to transactions and service_transactions

Revision ID: 0013_add_created_at_indexes
Revises: 0012_user_bvn_nin_hashes
Create Date: 2026-06-08 10:43:00.000000

"""
from typing import Sequence, Union
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0013_add_created_at_indexes'
down_revision: Union[str, None] = '0012_user_bvn_nin_hashes'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Index for transactions.created_at
    op.create_index(
        'ix_transactions_created_at',
        'transactions',
        ['created_at'],
        unique=False,
    )

    # Index for service_transactions.created_at
    op.create_index(
        'ix_service_transactions_created_at',
        'service_transactions',
        ['created_at'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_service_transactions_created_at', table_name='service_transactions')
    op.drop_index('ix_transactions_created_at', table_name='transactions')
