from functools import lru_cache
from pydantic import BaseSettings, AnyHttpUrl, PostgresDsn
from typing import Optional, List


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

    # Redis (optional)
    redis_url: Optional[str] = None

    # Paystack
    paystack_secret_key: str
    paystack_webhook_secret: str

    # Amigo API
    amigo_base_url: AnyHttpUrl
    amigo_api_key: str
    amigo_timeout_seconds: int = 15
    amigo_retry_count: int = 2

    # CORS
    cors_origins: List[str] = ["http://localhost:5173", "http://localhost:3000"]

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
