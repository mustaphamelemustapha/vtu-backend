"""add billstack to virtual_account_provider enum

Revision ID: f036be8e1a7b
Revises: cad7025451dd
Create Date: 2026-06-26 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f036be8e1a7b'
down_revision: Union[str, None] = 'cad7025451dd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        # Disable auto-commit for enum alter type
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE virtualaccountprovider ADD VALUE 'BILLSTACK'")
            op.execute("ALTER TYPE virtualaccountprovider ADD VALUE 'billstack'")


def downgrade() -> None:
    # PostgreSQL doesn't easily support dropping enum values without recreating the type,
    # so we pass here. Downgrade is not critical for this type addition.
    pass
