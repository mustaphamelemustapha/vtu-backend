from pydantic import BaseModel
from decimal import Decimal


class WalletOut(BaseModel):
    balance: Decimal
    is_locked: bool

    class Config:
        orm_mode = True


class FundWalletRequest(BaseModel):
    amount: Decimal
    callback_url: str | None = None


class LedgerOut(BaseModel):
    id: int
    amount: Decimal
    entry_type: str
    reference: str
    description: str

    class Config:
        orm_mode = True


class CreateBankTransferAccountsRequest(BaseModel):
    bvn: str | None = None
    nin: str | None = None


class BankAccountOut(BaseModel):
    bank_name: str
    account_number: str
    account_name: str | None = None


class BankTransferAccountsResponse(BaseModel):
    provider: str
    account_reference: str
    accounts: list[BankAccountOut]
    requires_kyc: bool = False
