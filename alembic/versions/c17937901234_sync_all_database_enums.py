"""sync all database enums with case-insensitive variants

Revision ID: c17937901234
Revises: b17937890123
Create Date: 2026-11-04 16:15:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c17937901234'
down_revision: Union[str, None] = 'b17937890123'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == 'postgresql':
        with op.get_context().autocommit_block():
            enum_map = {
                "ledgertype": ['credit', 'debit', 'CREDIT', 'DEBIT', 'Credit', 'Debit'],
                "transactiontype": [
                    'data', 'wallet_fund', 'wallet_transfer', 'airtime', 'cable', 'electricity', 'exam',
                    'DATA', 'WALLET_FUND', 'WALLET_TRANSFER', 'AIRTIME', 'CABLE', 'ELECTRICITY', 'EXAM'
                ],
                "transactionstatus": [
                    'pending', 'success', 'failed', 'refunded',
                    'PENDING', 'SUCCESS', 'FAILED', 'REFUNDED'
                ],
                "userrole": [
                    'customer', 'agent', 'ambassador', 'admin', 'user', 'reseller',
                    'CUSTOMER', 'AGENT', 'AMBASSADOR', 'ADMIN', 'USER', 'RESELLER'
                ],
                "disputestatus": [
                    'open', 'resolved', 'rejected',
                    'OPEN', 'RESOLVED', 'REJECTED'
                ],
                "virtualaccountprovider": [
                    'monnify', 'paystack', 'billstack', 'squad', 'safehaven', 'palmpay', 'moniepoint',
                    'MONNIFY', 'PAYSTACK', 'BILLSTACK', 'SQUAD', 'SAFEHAVEN', 'PALMPAY', 'MONIEPOINT'
                ],
                "virtualaccountstatus": [
                    'active', 'inactive', 'failed', 'pending',
                    'ACTIVE', 'INACTIVE', 'FAILED', 'PENDING'
                ],
                "agentrewardstatus": [
                    'pending', 'credited', 'failed',
                    'PENDING', 'CREDITED', 'FAILED'
                ]
            }
            for enum_name, values in enum_map.items():
                for val in values:
                    try:
                        op.execute(f"ALTER TYPE {enum_name} ADD VALUE IF NOT EXISTS '{val}'")
                    except Exception:
                        pass


def downgrade() -> None:
    pass

