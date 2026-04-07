from pydantic import BaseModel, Field

from app.schemas.notifications import BroadcastAnnouncementOut
from app.schemas.transaction import TransactionOut
from app.schemas.wallet import BankTransferAccountsResponse, WalletOut


class DashboardSummaryOut(BaseModel):
    wallet: WalletOut | None = None
    transactions: list[TransactionOut] = Field(default_factory=list)
    announcements: list[BroadcastAnnouncementOut] = Field(default_factory=list)
    bank_transfer_accounts: BankTransferAccountsResponse | None = None
    partial_failures: list[str] = Field(default_factory=list)
