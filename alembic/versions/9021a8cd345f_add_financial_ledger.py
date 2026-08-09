"""add_financial_ledger

Revision ID: 9021a8cd345f
Revises: f036be8e1a7b
Create Date: 2026-08-09 21:25:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '9021a8cd345f'
down_revision = 'f036be8e1a7b'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        'financial_ledger',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('reference', sa.String(), nullable=False, unique=True, index=True),
        sa.Column('source', sa.String(), nullable=True),
        sa.Column('status', sa.String(), nullable=True, default='SUCCESS'),
        sa.Column('category', sa.String(), nullable=False, index=True),
        sa.Column('entry_type', sa.String(), nullable=False),
        sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('party', sa.String(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

def downgrade():
    op.drop_table('financial_ledger')
