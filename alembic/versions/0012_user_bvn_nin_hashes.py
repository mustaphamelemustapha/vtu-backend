"""add bvn and nin hashes to users

Revision ID: 0012_user_bvn_nin_hashes
Revises: 0011_user_agent_upgrade_seen
Create Date: 2026-06-06 12:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0012_user_bvn_nin_hashes'
down_revision: Union[str, None] = '0011_user_agent_upgrade_seen'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('bvn_hash', sa.String(length=64), nullable=True))
    op.add_column('users', sa.Column('nin_hash', sa.String(length=64), nullable=True))
    op.create_index('ix_users_bvn_hash', 'users', ['bvn_hash'], unique=True)
    op.create_index('ix_users_nin_hash', 'users', ['nin_hash'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_users_nin_hash', table_name='users')
    op.drop_index('ix_users_bvn_hash', table_name='users')
    op.drop_column('users', 'nin_hash')
    op.drop_column('users', 'bvn_hash')
