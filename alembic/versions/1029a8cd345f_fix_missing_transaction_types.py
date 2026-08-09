"""fix_missing_transaction_types

Revision ID: 1029a8cd345f
Revises: 9021a8cd345f
Create Date: 2026-08-09 21:50:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '1029a8cd345f'
down_revision = '9021a8cd345f'
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        with op.get_context().autocommit_block():
            for val in ['airtime', 'cable', 'electricity', 'exam', 'wallet_transfer']:
                try:
                    op.execute(f"ALTER TYPE transactiontype ADD VALUE '{val}'")
                except Exception:
                    pass
            for val in ['AIRTIME', 'CABLE', 'ELECTRICITY', 'EXAM', 'WALLET_TRANSFER', 'DATA', 'WALLET_FUND']:
                try:
                    op.execute(f"ALTER TYPE transactiontype ADD VALUE '{val}'")
                except Exception:
                    pass
            for val in ['SUCCESS', 'PENDING', 'FAILED', 'REFUNDED']:
                try:
                    op.execute(f"ALTER TYPE transactionstatus ADD VALUE '{val}'")
                except Exception:
                    pass
            for val in ['REWARDED', 'PENDING', 'QUALIFIED']:
                try:
                    op.execute(f"ALTER TYPE referral_status ADD VALUE '{val}'")
                except Exception:
                    pass

def downgrade():
    pass
