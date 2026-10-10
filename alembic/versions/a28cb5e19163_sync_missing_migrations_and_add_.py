"""sync missing migrations and add integrations

Revision ID: a28cb5e19163
Revises: a8d3e91c7420
Create Date: 2026-10-09 17:34:30.099532

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector


# revision identifiers, used by Alembic.
revision: str = 'a28cb5e19163'
down_revision: Union[str, None] = 'a8d3e91c7420'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = Inspector.from_engine(conn)
    tables = inspector.get_table_names()

    if 'integration_providers' not in tables:
        op.create_table('integration_providers',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('identifier', sa.String(length=50), nullable=False),
        sa.Column('base_url', sa.String(length=255), nullable=True),
        sa.Column('api_key', sa.String(length=512), nullable=True),
        sa.Column('api_secret', sa.String(length=512), nullable=True),
        sa.Column('additional_config', sa.JSON(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('balance', sa.Float(), nullable=True),
        sa.Column('supported_services', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
        with op.batch_alter_table('integration_providers', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_integration_providers_identifier'), ['identifier'], unique=True)

    if 'payment_gateways' not in tables:
        op.create_table('payment_gateways',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('identifier', sa.String(length=50), nullable=False),
        sa.Column('public_key', sa.String(length=512), nullable=True),
        sa.Column('secret_key', sa.String(length=512), nullable=True),
        sa.Column('contract_code', sa.String(length=255), nullable=True),
        sa.Column('webhook_url', sa.String(length=255), nullable=True),
        sa.Column('additional_config', sa.JSON(), nullable=True),
        sa.Column('charge_percentage', sa.Float(), nullable=True),
        sa.Column('charge_flat', sa.Float(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
        with op.batch_alter_table('payment_gateways', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_payment_gateways_identifier'), ['identifier'], unique=True)

    # We skip all the other auto-generated column additions because app/main.py 
    # _ensure_... functions handle them in production and they cause DuplicateColumn exceptions.


def downgrade() -> None:
    # We leave downgrade empty so it doesn't break anything.
    pass
