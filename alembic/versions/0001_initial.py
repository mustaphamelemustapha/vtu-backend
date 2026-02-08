"""initial

Revision ID: 0001_initial
Revises: 
Create Date: 2026-02-08
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("role", sa.Enum("user", "reseller", "admin", name="userrole"), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("is_verified", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("reset_token", sa.String(128), nullable=True),
        sa.Column("reset_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_token", sa.String(128), nullable=True),
        sa.Column("verification_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_role_active", "users", ["role", "is_active"], unique=False)
    op.create_index("ix_users_reset_token", "users", ["reset_token"], unique=False)
    op.create_index("ix_users_verification_token", "users", ["verification_token"], unique=False)

    op.create_table(
        "wallets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False, unique=True),
        sa.Column("balance", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("is_locked", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_wallets_user_id", "wallets", ["user_id"], unique=False)

    op.create_table(
        "wallet_ledger",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("wallet_id", sa.Integer, sa.ForeignKey("wallets.id"), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("entry_type", sa.Enum("credit", "debit", name="ledgertype"), nullable=False),
        sa.Column("reference", sa.String(64), nullable=False),
        sa.Column("description", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_wallet_ledger_wallet_id_type", "wallet_ledger", ["wallet_id", "entry_type"], unique=False)
    op.create_index("ix_wallet_ledger_reference", "wallet_ledger", ["reference"], unique=False)

    op.create_table(
        "data_plans",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("network", sa.String(32), nullable=False),
        sa.Column("plan_code", sa.String(64), nullable=False),
        sa.Column("plan_name", sa.String(128), nullable=False),
        sa.Column("data_size", sa.String(32), nullable=False),
        sa.Column("validity", sa.String(32), nullable=False),
        sa.Column("base_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_data_plans_network_active", "data_plans", ["network", "is_active"], unique=False)
    op.create_unique_constraint("uq_data_plans_plan_code", "data_plans", ["plan_code"])

    op.create_table(
        "pricing_rules",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("network", sa.String(32), nullable=False),
        sa.Column("role", sa.Enum("user", "reseller", name="pricingrole"), nullable=False),
        sa.Column("margin", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_pricing_rules_network_role", "pricing_rules", ["network", "role"], unique=True)

    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reference", sa.String(64), nullable=False),
        sa.Column("network", sa.String(32), nullable=True),
        sa.Column("data_plan_code", sa.String(64), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("status", sa.Enum("pending", "success", "failed", "refunded", name="transactionstatus"), nullable=False),
        sa.Column("tx_type", sa.Enum("data", "wallet_fund", name="transactiontype"), nullable=False),
        sa.Column("external_reference", sa.String(64), nullable=True),
        sa.Column("failure_reason", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_transactions_reference", "transactions", ["reference"], unique=True)
    op.create_index("ix_transactions_user_status", "transactions", ["user_id", "status"], unique=False)
    op.create_index("ix_transactions_type_status", "transactions", ["tx_type", "status"], unique=False)

    op.create_table(
        "api_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("service", sa.String(64), nullable=False),
        sa.Column("endpoint", sa.String(255), nullable=False),
        sa.Column("status_code", sa.Integer, nullable=False),
        sa.Column("duration_ms", sa.Numeric(10, 2), nullable=False),
        sa.Column("reference", sa.String(64), nullable=True),
        sa.Column("success", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_api_logs_service_status", "api_logs", ["service", "status_code"], unique=False)


def downgrade():
    op.drop_table("api_logs")
    op.drop_table("transactions")
    op.drop_table("pricing_rules")
    op.drop_table("data_plans")
    op.drop_table("wallet_ledger")
    op.drop_table("wallets")
    op.drop_table("users")
    op.execute("DROP TYPE IF EXISTS transactionstatus")
    op.execute("DROP TYPE IF EXISTS transactiontype")
    op.execute("DROP TYPE IF EXISTS pricingrole")
    op.execute("DROP TYPE IF EXISTS ledgertype")
    op.execute("DROP TYPE IF EXISTS userrole")
