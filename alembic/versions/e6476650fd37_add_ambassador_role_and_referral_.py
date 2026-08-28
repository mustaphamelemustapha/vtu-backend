"""Add ambassador role and referral tracking fields

Revision ID: e6476650fd37
Revises: 1029a8cd345f
Create Date: 2026-08-28 12:01:21.953643

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e6476650fd37'
down_revision: Union[str, None] = '1029a8cd345f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new columns to referrals
    op.add_column('referrals', sa.Column('is_ten_percent_paid', sa.Boolean(), server_default='0', nullable=False))
    op.add_column('referrals', sa.Column('is_50gb_milestone_reached', sa.Boolean(), server_default='0', nullable=False))
    op.add_column('referrals', sa.Column('is_milestone_bonus_paid', sa.Boolean(), server_default='0', nullable=False))
    
    # Data migration for existing enum values
    op.execute("UPDATE users SET role = 'customer' WHERE role = 'user'")
    op.execute("UPDATE users SET role = 'agent' WHERE role = 'reseller'")

    # Alter column using batch operations for SQLite compatibility
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('role',
               existing_type=sa.VARCHAR(length=8),
               type_=sa.Enum('CUSTOMER', 'AGENT', 'AMBASSADOR', 'ADMIN', name='userrole'),
               existing_nullable=False)


def downgrade() -> None:
    # Alter column using batch operations for SQLite compatibility
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('role',
               existing_type=sa.Enum('CUSTOMER', 'AGENT', 'AMBASSADOR', 'ADMIN', name='userrole'),
               type_=sa.VARCHAR(length=8),
               existing_nullable=False)
               
    # Reverse data migration
    op.execute("UPDATE users SET role = 'user' WHERE role = 'customer'")
    op.execute("UPDATE users SET role = 'reseller' WHERE role = 'agent'")
    
    # Drop new columns from referrals
    op.drop_column('referrals', 'is_milestone_bonus_paid')
    op.drop_column('referrals', 'is_50gb_milestone_reached')
    op.drop_column('referrals', 'is_ten_percent_paid')
