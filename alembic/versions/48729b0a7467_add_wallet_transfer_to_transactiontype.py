"""Add WALLET_TRANSFER to TransactionType

Revision ID: 48729b0a7467
Revises: d9171f28b3cf
Create Date: 2026-07-06 15:40:00.145681

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '48729b0a7467'
down_revision: Union[str, None] = 'd9171f28b3cf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        with op.get_context().autocommit_block():
            try:
                op.execute("ALTER TYPE transactiontype ADD VALUE 'WALLET_TRANSFER'")
            except Exception:
                pass
            try:
                op.execute("ALTER TYPE virtualaccountprovider ADD VALUE 'BILLSTACK'")
            except Exception:
                pass
    else:
        with op.batch_alter_table('transactions') as batch_op:
            batch_op.alter_column('tx_type',
                   existing_type=sa.VARCHAR(length=11),
                   type_=sa.Enum('DATA', 'WALLET_FUND', 'WALLET_TRANSFER', 'AIRTIME', 'CABLE', 'ELECTRICITY', 'EXAM', name='transactiontype'),
                   existing_nullable=False)
        with op.batch_alter_table('virtual_accounts') as batch_op:
            batch_op.alter_column('provider',
                   existing_type=sa.VARCHAR(length=8),
                   type_=sa.Enum('MONNIFY', 'PAYSTACK', 'BILLSTACK', name='virtualaccountprovider'),
                   existing_nullable=False)


def downgrade() -> None:
    # Downgrading enums is complicated in Postgres, usually ignored
    pass
