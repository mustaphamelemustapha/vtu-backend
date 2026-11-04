"""add brand columns and userrole values

Revision ID: b17937890123
Revises: a28cb5e19163
Create Date: 2026-11-04 12:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector


# revision identifiers, used by Alembic.
revision: str = 'b17937890123'
down_revision: Union[str, None] = 'a28cb5e19163'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = Inspector.from_engine(conn)
    tables = set(inspector.get_table_names())
    is_postgres = conn.dialect.name == 'postgresql'

    # 1. Update userrole enum in PostgreSQL if needed
    if is_postgres:
        with op.get_context().autocommit_block():
            for val in ['customer', 'agent', 'ambassador', 'admin', 'user', 'reseller']:
                try:
                    op.execute(f"ALTER TYPE userrole ADD VALUE IF NOT EXISTS '{val}'")
                except Exception:
                    pass

    # 2. Add brand column to users table
    if 'users' in tables:
        user_cols = {c['name'] for c in inspector.get_columns('users')}
        if 'brand' not in user_cols:
            if is_postgres:
                op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS brand VARCHAR(32) DEFAULT 'meledata'")
            else:
                with op.batch_alter_table('users') as batch_op:
                    batch_op.add_column(sa.Column('brand', sa.String(length=32), nullable=True, server_default='meledata'))
        
        # Ensure index
        try:
            if is_postgres:
                op.execute("CREATE INDEX IF NOT EXISTS ix_users_brand ON users (brand)")
            else:
                with op.batch_alter_table('users') as batch_op:
                    batch_op.create_index('ix_users_brand', ['brand'])
        except Exception:
            pass

        # Backfill brand
        try:
            op.execute("UPDATE users SET brand = 'meledata' WHERE brand IS NULL OR brand = ''")
            op.execute("UPDATE users SET brand = 'kullomadata' WHERE email ILIKE '%kulloma%'")
            op.execute("UPDATE users SET brand = 'mzdata' WHERE email ILIKE '%mzdata%'")
        except Exception:
            pass

    # 3. Add brand column to financial_ledger table
    if 'financial_ledger' in tables:
        fl_cols = {c['name'] for c in inspector.get_columns('financial_ledger')}
        if 'brand' not in fl_cols:
            if is_postgres:
                op.execute("ALTER TABLE financial_ledger ADD COLUMN IF NOT EXISTS brand VARCHAR(32) DEFAULT 'meledata'")
            else:
                with op.batch_alter_table('financial_ledger') as batch_op:
                    batch_op.add_column(sa.Column('brand', sa.String(length=32), nullable=True, server_default='meledata'))
        try:
            if is_postgres:
                op.execute("CREATE INDEX IF NOT EXISTS ix_financial_ledger_brand ON financial_ledger (brand)")
        except Exception:
            pass

    # 4. Add brand column to broadcast_announcements table
    if 'broadcast_announcements' in tables:
        ba_cols = {c['name'] for c in inspector.get_columns('broadcast_announcements')}
        if 'brand' not in ba_cols:
            if is_postgres:
                op.execute("ALTER TABLE broadcast_announcements ADD COLUMN IF NOT EXISTS brand VARCHAR(32) DEFAULT 'all'")
            else:
                with op.batch_alter_table('broadcast_announcements') as batch_op:
                    batch_op.add_column(sa.Column('brand', sa.String(length=32), nullable=True, server_default='all'))
        try:
            if is_postgres:
                op.execute("CREATE INDEX IF NOT EXISTS ix_broadcast_announcements_brand ON broadcast_announcements (brand)")
        except Exception:
            pass


def downgrade() -> None:
    pass
