"""add admin audit log and service toggle

Revision ID: 0007_admin_audit_service_toggle
Revises: 0006_referral_dynamic_reward
Create Date: 2026-04-27
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_admin_audit_service_toggle"
down_revision = "0006_referral_dynamic_reward"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "service_toggles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("service_name", sa.String(length=50), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id")
    )
    op.create_index(op.f("ix_service_toggles_id"), "service_toggles", ["id"], unique=False)
    op.create_index(op.f("ix_service_toggles_service_name"), "service_toggles", ["service_name"], unique=True)

    op.create_table(
        "admin_audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("admin_email", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target", sa.String(length=100), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id")
    )
    op.create_index(op.f("ix_admin_audit_logs_admin_email"), "admin_audit_logs", ["admin_email"], unique=False)
    op.create_index(op.f("ix_admin_audit_logs_id"), "admin_audit_logs", ["id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_admin_audit_logs_id"), table_name="admin_audit_logs")
    op.drop_index(op.f("ix_admin_audit_logs_admin_email"), table_name="admin_audit_logs")
    op.drop_table("admin_audit_logs")
    op.drop_index(op.f("ix_service_toggles_service_name"), table_name="service_toggles")
    op.drop_index(op.f("ix_service_toggles_id"), table_name="service_toggles")
    op.drop_table("service_toggles")
