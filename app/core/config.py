from functools import lru_cache
import json
from typing import Optional

from pydantic import AnyHttpUrl, BaseSettings, PostgresDsn


def parse_cors_origins(value: str) -> list[str]:
    if not value:
        return []

    parsed: list[str]
    raw = value.strip()
    if raw.startswith("["):
        try:
            items = json.loads(raw)
            parsed = [str(item).strip() for item in items if str(item).strip()]
        except (TypeError, ValueError):
            parsed = []
    else:
        parsed = [origin.strip() for origin in raw.split(",") if origin.strip()]

    # Preserve order and remove duplicates.
    return list(dict.fromkeys(parsed))


class Settings(BaseSettings):
    app_name: str = "VTU SaaS"
    environment: str = "development"
    api_v1_prefix: str = "/api/v1"

    # Security
    secret_key: str
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    password_bcrypt_rounds: int = 12

    # Database
    database_url: PostgresDsn
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_pool_timeout: int = 15
    db_pool_recycle: int = 1200
    db_pool_pre_ping: bool = True

    # Redis (optional)
    redis_url: Optional[str] = None

    # Paystack
    paystack_secret_key: str
    paystack_webhook_secret: str

    # Monnify
    monnify_api_key: str
    monnify_secret_key: str
    monnify_contract_code: str
    monnify_base_url: AnyHttpUrl
    monnify_webhook_secret: Optional[str] = None
    monnify_currency: str = "NGN"
    monnify_payment_methods: str = "CARD,ACCOUNT_TRANSFER,USSD"

    # Amigo API
    amigo_base_url: AnyHttpUrl
    amigo_api_key: str
    amigo_timeout_seconds: int = 15
    amigo_retry_count: int = 2
    amigo_test_mode: bool = False
    # Provider endpoint paths (Amigo deployments differ; keep these configurable).
    amigo_data_purchase_path: str = "/data/"
    amigo_plans_path: str = "/plans/efficiency"

    # Frontend URLs (used for email links)
    frontend_base_url: str = "http://localhost:5173"

    # Email (password reset)
    email_provider: str = "console"  # console|resend|smtp|brevo
    email_from: str = "AxisVTU <no-reply@axisvtu.local>"

    # Resend
    resend_api_key: Optional[str] = None

    # Brevo
    brevo_api_key: Optional[str] = None

    # SMTP
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_use_tls: bool = True

    # CORS
    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    auto_create_tables: bool = False

    # Ops: bootstrap admin users (comma-separated emails). Useful when the platform
    # doesn't provide a shell/psql access on free plans.
    bootstrap_admin_emails: str = ""

    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
