from pydantic import BaseModel
from decimal import Decimal


class WalletOut(BaseModel):
    balance: Decimal
    is_locked: bool

    class Config:
        orm_mode = True


class FundWalletRequest(BaseModel):
    amount: Decimal
    callback_url: str


class LedgerOut(BaseModel):
    id: int
    amount: Decimal
    entry_type: str
    reference: str
    description: str

    class Config:
        orm_mode = True
