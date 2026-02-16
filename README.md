# VTU SaaS Backend (Nigeria)

## Overview
Production-ready FastAPI backend for a Nigerian VTU platform. Core modules include auth, wallet, VTU data purchase, pricing engine, Paystack webhook handling, admin tools, and API logging.

## Architecture
- `app/api/v1/endpoints`: REST controllers
- `app/services`: business logic (Amigo, pricing, wallet, Paystack)
- `app/models`: SQLAlchemy models
- `app/schemas`: Pydantic DTOs
- `app/middlewares`: rate limiting
- `alembic`: migrations

## Environment Variables
See `.env.example` for the full list.
- `MONNIFY_*` values are required because Monnify routes are enabled.
- `CORS_ORIGINS` supports comma-separated values (e.g., `http://localhost:5173,http://localhost:3000`).
- `AUTO_CREATE_TABLES` is optional and defaults to `false`; prefer Alembic migrations.
- Database pool tuning (important in production):
  - `DB_POOL_SIZE` (default `5`)
  - `DB_MAX_OVERFLOW` (default `5`)
  - `DB_POOL_TIMEOUT` seconds (default `15`)
  - `DB_POOL_RECYCLE` seconds (default `1200`)
  - `DB_POOL_PRE_PING` (default `true`)

## Setup (Local)
1. Use Python `3.11` (recommended) and create/activate a virtualenv.
2. Install deps: `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and set values.
4. Run migrations: `alembic upgrade head`
5. Start API: `uvicorn app.main:app --reload`

## Docker
`docker compose up --build`

The compose setup runs `alembic upgrade head` before starting the API.

## API Documentation
FastAPI docs:
- Swagger UI: `/docs`
- OpenAPI JSON: `/openapi.json`

### Core Endpoints (v1)
- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/refresh`
- `POST /api/v1/auth/forgot-password`
- `POST /api/v1/auth/reset-password`
- `POST /api/v1/auth/verify-email`
- `GET /api/v1/wallet/me`
- `POST /api/v1/wallet/fund`
- `POST /api/v1/wallet/paystack/webhook`
- `GET /api/v1/data/plans`
- `POST /api/v1/data/purchase`
- `POST /api/v1/data/sync`
- `GET /api/v1/transactions/me`
- `GET /api/v1/admin/analytics`
- `POST /api/v1/admin/fund-wallet`
- `POST /api/v1/admin/pricing`
- `POST /api/v1/admin/users/{user_id}/suspend`
- `POST /api/v1/admin/users/{user_id}/activate`

### Ops Endpoints
- `GET /healthz` (liveness)
- `GET /readyz` (database readiness)
- Monitoring runbook: `docs/OPERATIONS_MONITORING.md`
- 4-day launch checklist: `docs/LAUNCH_4_DAY_CHECKLIST.md`
- Local endpoint check: `PROD_BACKEND_BASE_URL=https://<your-domain> python scripts/check_health.py`
- Production smoke run: `python scripts/prod_smoke.py --base-url https://<your-domain>`

## Sample Amigo API Payloads
Fetch data plans (example response):
```json
{
  "status": "success",
  "data": [
    {
      "plan_code": "MTN_1GB_30D",
      "network": "mtn",
      "plan_name": "MTN 1GB",
      "data_size": "1GB",
      "validity": "30d",
      "price": 350
    }
  ]
}
```

Purchase data (request):
```json
{
  "plan_code": "MTN_1GB_30D",
  "phone_number": "08031234567",
  "reference": "DATA_abcdef12"
}
```

Purchase data (response):
```json
{
  "status": "success",
  "transaction_id": "AMIGO_123456",
  "message": "Data sent"
}
```

## Security Checklist
- Password hashing using bcrypt
- JWT access + refresh tokens
- Role-based access control (RBAC)
- Rate limiting middleware
- Input validation via Pydantic
- Secure Paystack webhook verification
- Automatic refund on failed data purchase
- Wallet locking mechanism (model flag)

## Deployment Guide (VPS/Railway/Render)
- Set environment variables in the platform UI
- Use managed PostgreSQL
- Apply migrations on deploy
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port 8000`

## Notes
- Email sending is stubbed: integrate with a transactional email provider (e.g., Postmark, SendGrid) for reset/verification.
- Redis caching is optional and not required for core functionality.
