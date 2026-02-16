# Operations Monitoring Runbook

This runbook defines the production monitoring setup for AxisVTU backend and the first-response actions when incidents happen.

## Endpoints to Monitor

- Liveness: `GET /healthz`
  - Expected: `200` and JSON with `"status":"ok"`
- Readiness: `GET /readyz`
  - Expected: `200` and JSON with `"status":"ready"`
  - If DB is unavailable, this should return `503`.

Example:

- `https://<your-backend-domain>/healthz`
- `https://<your-backend-domain>/readyz`

## UptimeRobot Setup (Primary)

Create two HTTP(s) monitors:

1. `AxisVTU Backend Liveness`
   - URL: `/healthz`
   - Method: `GET`
   - Interval: `5 minutes`
   - Timeout: `30 seconds`
   - Alert contacts: your email + WhatsApp/Telegram if available

2. `AxisVTU Backend Readiness`
   - URL: `/readyz`
   - Method: `GET`
   - Interval: `5 minutes`
   - Timeout: `30 seconds`
   - Alert contacts: same as above

Recommended alert policy:

- Trigger after `2 consecutive failures`
- Re-alert every `30 minutes` until resolved

## Better Stack Setup (Secondary)

Create same two monitors:

- Monitor 1: `/healthz` (warning)
- Monitor 2: `/readyz` (critical)

Recommended heartbeat:

- Check frequency: `60s` or `120s` if plan allows
- Incident auto-resolve enabled
- Escalation: email -> SMS/phone (if available)

## GitHub Actions Healthcheck (Optional but Useful)

This repo includes a scheduled workflow (`.github/workflows/healthcheck.yml`).

Set repository secret:

- `PROD_BACKEND_BASE_URL` = `https://<your-backend-domain>`

It will run every 30 minutes and on manual trigger, validating both `/healthz` and `/readyz`.

You can also run the same check locally:

- `PROD_BACKEND_BASE_URL=https://<your-backend-domain> python scripts/check_health.py`

## GitHub Actions Production Smoke (Recommended)

This repo includes `/.github/workflows/prod-smoke.yml`:

- Runs every 6 hours and on manual trigger
- Executes auth/wallet smoke checks using `scripts/prod_smoke.py`
- Reuses the same repository secret:
  - `PROD_BACKEND_BASE_URL=https://<your-backend-domain>`

## First Response Playbook

When alerts fire:

1. Confirm issue quickly:
   - Open `/healthz`
   - Open `/readyz`
2. If `/healthz` fails:
   - Check Render service status and latest deploy
   - Restart service
3. If `/readyz` fails but `/healthz` passes:
   - Suspect DB connectivity/pool exhaustion
   - Check recent logs for `QueuePool limit` / timeout errors
   - Verify DB pool env values:
     - `DB_POOL_SIZE=5`
     - `DB_MAX_OVERFLOW=5`
     - `DB_POOL_TIMEOUT=15`
     - `DB_POOL_RECYCLE=1200`
     - `DB_POOL_PRE_PING=true`
   - Redeploy backend
4. Verify recovery:
   - `/readyz` returns `200`
   - login/register flow succeeds

## 4-Day Readiness Plan

Day 1:

- Enable UptimeRobot monitors
- Enable Better Stack monitors (or keep as backup plan)
- Set `PROD_BACKEND_BASE_URL` GitHub secret

Day 2:

- Simulate a failure (temporary bad DB env) and verify alerts arrive
- Verify incident response timing and alert routing

Day 3:

- Review logs for top recurring warnings/errors
- Fix any high-frequency issue before launch window

Day 4:

- Final production smoke test:
  - register
  - login
  - wallet load page
  - buy data
  - profile update
- Confirm monitors green for at least 2-4 hours continuously
