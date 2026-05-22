"""add_perf_indexes - api_logs.reference and admin_audit_logs.target

Revision ID: 0009_add_perf_indexes
Revises: d28149582b84
Create Date: 2026-05-22

These two indexes speed up:
  - /admin/transactions/{reference}  (ApiLog lookup by reference)
  - /admin/audit-logs?reference=...  (AdminAuditLog lookup by target/reference)
"""
from typing import Sequence, Union
from alembic import op


# revision identifiers
revision: str = '0009_add_perf_indexes'
down_revision: Union[str, None] = 'd28149582b84'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Index for ApiLog.reference — used in get_transaction_details to find the
    # latest API log linked to a transaction. Without this, it's a full table scan.
    op.create_index(
        'ix_api_logs_reference',
        'api_logs',
        ['reference'],
        unique=False,
    )

    # Index for AdminAuditLog.target — used in get_audit_logs when filtering by
    # ?reference=... so we don't scan every audit log row.
    op.create_index(
        'ix_admin_audit_logs_target',
        'admin_audit_logs',
        ['target'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_admin_audit_logs_target', table_name='admin_audit_logs')
    op.drop_index('ix_api_logs_reference', table_name='api_logs')
