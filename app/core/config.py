from functools import lru_cache
import json
from decimal import Decimal
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
    secret_key: str = ""
    access_token_expire_minutes: int = 144000
    refresh_token_expire_days: int = 7
    password_bcrypt_rounds: int = 12
    pin_bcrypt_rounds: int = 12
    pin_max_failed_attempts: int = 5
    pin_lock_minutes: int = 15
    pin_reset_token_minutes: int = 30

    # Database
    database_url: str = "sqlite:///./test.db"
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_pool_timeout: int = 15
    db_pool_recycle: int = 1200
    db_pool_pre_ping: bool = True

    # Redis (optional)
    redis_url: str = ""

    # Cloudinary (optional)
    cloudinary_url: str = ""

    # Paystack
    paystack_secret_key: str = ""
    paystack_webhook_secret: str = ""
    paystack_dedicated_enabled: bool = False
    paystack_dedicated_preferred_bank: str = "titan-paystack"

    # Bank transfer provider (monnify|paystack)
    bank_transfer_provider: str = "monnify"

    # Monnify
    monnify_api_key: str = ""
    monnify_secret_key: str = ""
    monnify_contract_code: str = ""
    monnify_base_url: str = "https://sandbox.monnify.com"
    monnify_webhook_secret: str = ""
    monnify_currency: str = "NGN"
    monnify_payment_methods: str = "CARD,ACCOUNT_TRANSFER,USSD"

    # Billstack
    billstack_api_key: str = ""
    billstack_webhook_secret: str = ""
    billstack_enabled: bool = False
    billstack_preferred_bank: str = "9PSB"

    # Aspfiy
    aspfiy_secret_key: str = ""
    aspfiy_base_url: str = "https://api-v1.aspfiy.com"
    aspfiy_enabled: bool = False
    aspfiy_webhook_url: str = "https://vtu-backend-8gsi.onrender.com/api/v1/webhooks/aspfiy"

    # Amigo API
    amigo_base_url: str = "https://amigo.ng/api"
    amigo_api_key: str = ""
    amigo_timeout_seconds: int = 15
    amigo_retry_count: int = 2
    amigo_test_mode: bool = False
    # Provider endpoint paths (Amigo deployments differ; keep these configurable).
    amigo_data_purchase_path: str = "/data/"
    amigo_plans_path: str = "/plans/"
    pending_reconcile_enabled: bool = True
    pending_reconcile_interval_seconds: int = 25
    pending_reconcile_batch_size: int = 30
    pending_reconcile_min_age_seconds: int = 5
    # If a data transaction stays pending beyond this window with no definitive
    # provider failure signal, we settle it as success to prevent false-negative
    # customer experience for already-delivered data.
    pending_reconcile_auto_success_seconds: int = 120
    promo_mtn_1gb_enabled: bool = False
    promo_mtn_1gb_limit: int = 50
    promo_mtn_1gb_price: Decimal = Decimal("199")
    promo_mtn_1gb_network: str = "mtn"
    promo_mtn_1gb_plan_code: str = "1001"

    # VTPass API (airtime, cable, electricity, exam)
    vtpass_base_url: str = "https://vtpass.com/api"
    vtpass_api_key: str = ""
    vtpass_public_key: str = ""
    vtpass_secret_key: str = ""
    vtpass_timeout_seconds: int = 20
    vtpass_enabled: bool = False

    # Bills provider routing:
    # - auto: prefer ClubKonnect when enabled, else VTPass, else mock
    # - clubkonnect|vtpass|autosync|mock: force a specific provider
    bills_provider: str = "auto"

    # ClubKonnect / NelloByte API
    clubkonnect_base_url: str = "https://www.nellobytesystems.com"
    clubkonnect_user_id: str = ""
    clubkonnect_api_key: str = ""
    nello_user_id: str = ""
    nello_api_key: str = ""
    clubkonnect_timeout_seconds: int = 20
    clubkonnect_enabled: bool = False
    clubkonnect_callback_url: str = ""

    # SMEPlug API
    smeplug_base_url: str = "https://smeplug.ng/api/v1"
    smeplug_api_key: str = ""
    smeplug_network_airtel: int = 2
    smeplug_webhook_secret: str = ""

    # Autosync API
    autosync_base_url: str = "https://autosyncng.com/api"
    autosync_api_key: str = ""
    autosync_webhook_pin: str = ""

    # Boltnet API
    boltnet_base_url: str = "https://boltnet.com.ng"
    boltnet_api_key: str = ""
    boltnet_timeout_seconds: int = 30

    # MZDATA API
    mzdata_base_url: str = "https://mzdata-1.onrender.com/api/v1"
    mzdata_api_key: str = ""

    # Telecom Abode API
    telecom_abode_base_url: str = "https://telecomabode.com.ng/api"
    telecom_abode_api_key: str = ""
    telecom_abode_enabled: bool = False
    telecom_abode_timeout_seconds: int = 30

    # Fraud / abuse guardrails for purchases
    fraud_guard_enabled: bool = True
    fraud_single_tx_limit_ngn: Decimal = Decimal("50000")
    fraud_daily_total_limit_ngn: Decimal = Decimal("200000")
    fraud_daily_purchase_count_limit: int = 25

    # Frontend URLs (used for email links)
    frontend_base_url: str = "http://localhost:5173"

    # App Update Config
    min_app_version: str = "1.0.0"
    latest_app_version: str = "1.0.0"
    play_store_url: str = "https://play.google.com/store/apps/details?id=com.mmtech.axisvtu"
    app_store_url: str = "https://apps.apple.com/ng/app/mele-data/id6779367547"

    # Email (password reset)
    email_provider: str = "console"  # console|resend|smtp|brevo
    email_from: str = "KULLOMA DATA <no-reply@axisvtu.local>"

    # Resend
    resend_api_key: str = ""

    # Brevo
    brevo_api_key: str = ""

    # SMTP
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True

    # CORS
    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    auto_create_tables: bool = False

    # Ops: bootstrap admin users (comma-separated emails). Useful when the platform
    # doesn't provide a shell/psql access on free plans.
    bootstrap_admin_emails: str = ""

    # Termii OTP / SMS
    termii_api_key: str = ""
    termii_base_url: str = "https://v4.api.termii.com/"
    termii_sender_id: str = "N-Alert"

    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
