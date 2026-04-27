"""Add display_price to data_plans and margin_type to pricing_rules

Revision ID: 0008_pricing_control
Revises: 0007_admin_audit_service_toggle
Create Date: 2026-04-27
"""

from alembic import op
import sqlalchemy as sa

revision = "0008_pricing_control"
down_revision = "0007_admin_audit_service_toggle"
branch_labels = None
depends_on = None


def upgrade():
    # Add nullable display_price override to data_plans.
    # When set by admin, this is used as the selling price instead of base_price + margin.
    with op.batch_alter_table("data_plans") as batch_op:
        batch_op.add_column(
            sa.Column("display_price", sa.Numeric(precision=12, scale=2), nullable=True)
        )

    # Add margin_type to pricing_rules.
    # 'fixed' = flat amount added to base; 'percentage' = % of base added.
    # Default 'fixed' maintains backward-compatibility with existing rules.
    with op.batch_alter_table("pricing_rules") as batch_op:
        batch_op.add_column(
            sa.Column(
                "margin_type",
                sa.String(16),
                nullable=False,
                server_default="fixed",
            )
        )


def downgrade():
    with op.batch_alter_table("pricing_rules") as batch_op:
        batch_op.drop_column("margin_type")

    with op.batch_alter_table("data_plans") as batch_op:
        batch_op.drop_column("display_price")
