"""Add state column to users

Revision ID: 83b9e4a1c5d0
Revises: 75d718ff057d
Create Date: 2026-10-01 14:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '83b9e4a1c5d0'
down_revision: Union[str, None] = '75d718ff057d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('state', sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'state')
