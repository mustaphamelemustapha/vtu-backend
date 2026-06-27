"""add developer columns

Revision ID: d9171f28b3cf
Revises: f036be8e1a7b
Create Date: 2026-06-27 10:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd9171f28b3cf'
down_revision: Union[str, None] = 'f036be8e1a7b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('is_developer', sa.Boolean(), server_default='0', nullable=False))
    op.add_column('users', sa.Column('developer_status', sa.String(length=32), server_default='none', nullable=False))
    op.add_column('users', sa.Column('api_public_key', sa.String(length=64), nullable=True))
    op.add_column('users', sa.Column('api_secret_key_hash', sa.String(length=128), nullable=True))
    op.create_index('ix_users_api_public_key', 'users', ['api_public_key'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_users_api_public_key', table_name='users')
    op.drop_column('users', 'api_secret_key_hash')
    op.drop_column('users', 'api_public_key')
    op.drop_column('users', 'developer_status')
    op.drop_column('users', 'is_developer')
