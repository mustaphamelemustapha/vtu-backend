"""add referrals system

Revision ID: 0004_referrals
Revises: 0003_clear_stale_pin_locks
Create Date: 2026-04-23
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_referrals"
down_revision = "0003_clear_stale_pin_locks"
branch_labels = None
depends_on = None


def _base36(value: int) -> str:
    digits = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if value <= 0:
        return "0"
    result = []
    number = int(value)
    while number:
        number, rem = divmod(number, 36)
        result.append(digits[rem])
    return "".join(reversed(result))


def upgrade():
    op.add_column("users", sa.Column("referral_code", sa.String(length=16), nullable=True))
    op.add_column("users", sa.Column("referred_by_id", sa.Integer(), nullable=True))

    bind = op.get_bind()
    user_rows = bind.execute(sa.text("SELECT id FROM users ORDER BY id ASC")).fetchall()
    for row in user_rows:
        code = f"AX{_base36(int(row.id))}"
        bind.execute(
            sa.text("UPDATE users SET referral_code = :code WHERE id = :id AND (referral_code IS NULL OR referral_code = '')"),
            {"code": code, "id": int(row.id)},
        )

    op.alter_column("users", "referral_code", nullable=False)
    op.create_index("ix_users_referral_code", "users", ["referral_code"], unique=True)
    op.create_index("ix_users_referred_by_id", "users", ["referred_by_id"], unique=False)
    op.create_foreign_key("fk_users_referred_by_id_users", "users", "users", ["referred_by_id"], ["id"])

    referral_status = sa.Enum("pending", "qualified", "rewarded", name="referral_status")
    referral_status.create(bind, checkfirst=True)

    op.create_table(
        "referrals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("referrer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("referred_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, unique=True),
        sa.Column("referral_code_used", sa.String(length=32), nullable=False),
        sa.Column("accumulated_mb", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("target_mb", sa.Integer(), nullable=False, server_default="51200"),
        sa.Column("reward_amount", sa.Numeric(12, 2), nullable=False, server_default="2000"),
        sa.Column("status", referral_status, nullable=False, server_default="pending"),
        sa.Column("qualifying_transaction_reference", sa.String(length=64), nullable=True),
        sa.Column("reward_transaction_reference", sa.String(length=64), nullable=True),
        sa.Column("qualified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rewarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_referrals_referrer_status", "referrals", ["referrer_id", "status"], unique=False)
    op.create_index("ix_referrals_referred_user", "referrals", ["referred_user_id"], unique=True)
    op.create_index("ix_referrals_reward_reference", "referrals", ["reward_transaction_reference"], unique=False)

    op.create_table(
        "referral_contributions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("referral_id", sa.Integer(), sa.ForeignKey("referrals.id"), nullable=False),
        sa.Column("transaction_reference", sa.String(length=64), nullable=False, unique=True),
        sa.Column("mb", sa.Integer(), nullable=False),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_referral_contributions_referral_reversed",
        "referral_contributions",
        ["referral_id", "reversed_at"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_referral_contributions_referral_reversed", table_name="referral_contributions")
    op.drop_table("referral_contributions")
    op.drop_index("ix_referrals_reward_reference", table_name="referrals")
    op.drop_index("ix_referrals_referred_user", table_name="referrals")
    op.drop_index("ix_referrals_referrer_status", table_name="referrals")
    op.drop_table("referrals")
    bind = op.get_bind()
    referral_status = sa.Enum(name="referral_status")
    referral_status.drop(bind, checkfirst=True)
    op.drop_constraint("fk_users_referred_by_id_users", "users", type_="foreignkey")
    op.drop_index("ix_users_referred_by_id", table_name="users")
    op.drop_index("ix_users_referral_code", table_name="users")
    op.drop_column("users", "referred_by_id")
    op.drop_column("users", "referral_code")
