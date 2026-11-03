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
    phone_number: str | None = None


class BankAccountOut(BaseModel):
    bank_name: str
    account_number: str
    account_name: str | None = None


class BankTransferAccountsResponse(BaseModel):
    provider: str
    account_reference: str
    accounts: list[BankAccountOut]
    requires_kyc: bool = False
    requires_phone: bool = False
    message: str | None = None

class TransferVerifyRequest(BaseModel):
    identifier: str

class TransferVerifyResponse(BaseModel):
    full_name: str

class TransferRequest(BaseModel):
    identifier: str
    amount: Decimal


class BankOut(BaseModel):
    name: str
    code: str
    slug: str | None = None
    active: bool = True


class BankResolveRequest(BaseModel):
    account_number: str
    bank_code: str


class BankResolveResponse(BaseModel):
    account_number: str
    account_name: str
    bank_id: int | None = None


class BankWithdrawalRequest(BaseModel):
    account_number: str
    bank_code: str
    bank_name: str
    account_name: str
    amount: Decimal
    pin: str
    narration: str | None = None


class BankWithdrawalResponse(BaseModel):
    status: str
    message: str
    reference: str
    provider_reference: str | None = None
    session_id: str | None = None
    created_at: str | None = None
    amount: Decimal
    fee: Decimal
    total_debited: Decimal
    bank_name: str
    account_number: str
    account_name: str


class LinkKycRequest(BaseModel):
    bvn: str | None = None
    nin: str | None = None


class LinkKycResponse(BaseModel):
    status: bool = True
    message: str
    kyc_verified: bool = True
    has_bvn: bool = False
    has_nin: bool = False

