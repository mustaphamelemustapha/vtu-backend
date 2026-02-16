import os


def _set_test_env() -> None:
    defaults = {
        "APP_NAME": "VTU SaaS Test",
        "ENVIRONMENT": "test",
        "SECRET_KEY": "test-secret",
        "ACCESS_TOKEN_EXPIRE_MINUTES": "30",
        "REFRESH_TOKEN_EXPIRE_DAYS": "7",
        "PASSWORD_BCRYPT_ROUNDS": "4",
        "AUTO_CREATE_TABLES": "false",
        "DATABASE_URL": "postgresql://user:password@localhost:5432/vtu_test",
        "REDIS_URL": "",
        "PAYSTACK_SECRET_KEY": "sk_test_xxx",
        "PAYSTACK_WEBHOOK_SECRET": "whsec_test_xxx",
        "MONNIFY_API_KEY": "monnify_api_key",
        "MONNIFY_SECRET_KEY": "monnify_secret_key",
        "MONNIFY_CONTRACT_CODE": "1234567890",
        "MONNIFY_BASE_URL": "https://sandbox.monnify.com",
        "MONNIFY_WEBHOOK_SECRET": "monnify_webhook_secret",
        "MONNIFY_CURRENCY": "NGN",
        "MONNIFY_PAYMENT_METHODS": "CARD,ACCOUNT_TRANSFER,USSD",
        "AMIGO_BASE_URL": "https://amigo.ng/api",
        "AMIGO_API_KEY": "amigo_key",
        "AMIGO_TIMEOUT_SECONDS": "15",
        "AMIGO_RETRY_COUNT": "2",
        "AMIGO_TEST_MODE": "true",
        "CORS_ORIGINS": "http://localhost:5173,http://localhost:3000",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)


_set_test_env()
