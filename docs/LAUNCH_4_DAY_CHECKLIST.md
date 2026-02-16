# AxisVTU Launch Checklist (4 Days)

Use this as the execution checklist before public launch.

## Day 1: Monitoring + Stability Baseline

- Set backend environment variables for DB pooling:
  - `DB_POOL_SIZE=5`
  - `DB_MAX_OVERFLOW=5`
  - `DB_POOL_TIMEOUT=15`
  - `DB_POOL_RECYCLE=1200`
  - `DB_POOL_PRE_PING=true`
- Set GitHub repository secret:
  - `PROD_BACKEND_BASE_URL=https://<your-render-backend-domain>`
- Run GitHub Action `Backend Healthcheck` manually once.
- Configure uptime monitors:
  - `/healthz`
  - `/readyz`

## Day 2: Auth + Wallet Reliability

- Test in production:
  - Register
  - Login
  - Forgot password
  - Reset password
- Test wallet flow:
  - Fund wallet
  - Verify payment callback
  - Confirm wallet balance update
- Run automated smoke:
  - `python scripts/prod_smoke.py --base-url https://<your-backend-domain>`
- Confirm no 500s in Render logs during tests.

## Day 3: Product QA Sweep

- Test all user modules end-to-end:
  - Data purchase
  - Airtime purchase
  - Electricity
  - Cable TV
  - Exam PIN
  - Transactions history
  - Profile update
- Verify responsive UI on small phones and desktop.
- Confirm PWA install works on Android Chrome.

## Day 4: Launch Readiness Gate

- Keep monitors green for at least 4 continuous hours.
- Confirm CI status is green:
  - Backend tests
  - Frontend build and e2e smoke
- Confirm admin visibility for transactions and pricing controls.
- Freeze non-critical changes and launch.

## Severity Rules During Countdown

- P0: Login/register/payment outage -> block launch.
- P1: Wrong pricing/billing data -> block launch.
- P2: UI misalignment/non-critical style issues -> can launch with follow-up ticket.
