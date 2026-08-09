from sqlalchemy.dialects import postgresql
from sqlalchemy import select
from app.models.transaction import Transaction, TransactionType

stmt = select(Transaction).where(Transaction.tx_type == TransactionType.DATA)
print(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

stmt_in = select(Transaction).where(Transaction.tx_type.in_([TransactionType.DATA, TransactionType.AIRTIME]))
print(stmt_in.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
