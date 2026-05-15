"""add fcm_token to users

Revision ID: 7ffa1244f905
Revises: 0008_pricing_control
Create Date: 2026-05-15 10:09:35.420665

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7ffa1244f905'
down_revision: Union[str, None] = '0008_pricing_control'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('fcm_token', sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'fcm_token')
