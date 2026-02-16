#!/usr/bin/env python3
"""AxisVTU production smoke checks for Day 2 launch readiness."""

from __future__ import annotations

import argparse
import random
import string
import time
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class SmokeContext:
    base_url: str
    api_prefix: str
    timeout_seconds: float
    verify_tls: bool
    retries: int
    retry_delay_seconds: float


def _random_suffix(length: int = 6) -> str:
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choice(chars) for _ in range(length))


def _api_url(ctx: SmokeContext, path: str) -> str:
    return f"{ctx.base_url}{ctx.api_prefix}{path}"


def _step(name: str) -> None:
    print(f"\n==> {name}")


def _assert_status(resp: httpx.Response, expected: int, step_name: str) -> None:
    if resp.status_code != expected:
        body = resp.text
        raise RuntimeError(
            f"{step_name} failed: expected HTTP {expected}, got {resp.status_code}. Body: {body}"
        )


def _request(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    step_name: str,
    expected_status: int = 200,
    **kwargs: Any,
) -> httpx.Response:
    retries = int(kwargs.pop("retries", 0))
    retry_delay_seconds = float(kwargs.pop("retry_delay_seconds", 0.0))
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = client.request(method, url, **kwargs)
            if resp.status_code in {502, 503, 504} and attempt < retries:
                print(
                    f"{step_name}: transient HTTP {resp.status_code}, "
                    f"retrying ({attempt + 1}/{retries})..."
                )
                time.sleep(retry_delay_seconds)
                continue
            _assert_status(resp, expected_status, step_name)
            return resp
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
            last_error = exc
            if attempt >= retries:
                raise RuntimeError(f"{step_name} request failed: {exc}") from exc
            print(f"{step_name}: transient error ({exc}), retrying ({attempt + 1}/{retries})...")
            time.sleep(retry_delay_seconds)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"{step_name} request failed: {exc}") from exc
    if last_error:
        raise RuntimeError(f"{step_name} request failed: {last_error}") from last_error
    raise RuntimeError(f"{step_name} failed unexpectedly.")


def run_smoke(
    *,
    base_url: str,
    api_prefix: str,
    email: str | None,
    password: str | None,
    full_name: str,
    timeout_seconds: float,
    verify_tls: bool,
    retries: int,
    retry_delay_seconds: float,
    check_wallet_fund: bool,
    check_forgot_password: bool,
    cleanup_created_user: bool,
) -> None:
    ctx = SmokeContext(
        base_url=base_url.rstrip("/"),
        api_prefix="/" + api_prefix.strip("/"),
        timeout_seconds=timeout_seconds,
        verify_tls=verify_tls,
        retries=retries,
        retry_delay_seconds=retry_delay_seconds,
    )

    run_id = f"{int(time.time())}-{_random_suffix()}"
    created_user = False
    access_token = ""
    refresh_token = ""
    using_existing_user = bool(email and password)

    if email and not password:
        raise RuntimeError("If --email is provided, --password must also be provided.")
    if password and not email:
        raise RuntimeError("If --password is provided, --email must also be provided.")

    if not using_existing_user:
        email = f"axisvtu.smoke+{run_id}@example.com"
        password = f"SmokePass{_random_suffix(4)}!123"

    with httpx.Client(timeout=ctx.timeout_seconds, verify=ctx.verify_tls) as client:
        _step("Health checks")
        healthz = _request(
            client,
            "GET",
            f"{ctx.base_url}/healthz",
            step_name="GET /healthz",
            retries=ctx.retries,
            retry_delay_seconds=ctx.retry_delay_seconds,
        )
        readyz = _request(
            client,
            "GET",
            f"{ctx.base_url}/readyz",
            step_name="GET /readyz",
            retries=ctx.retries,
            retry_delay_seconds=ctx.retry_delay_seconds,
        )
        print(f"/healthz -> {healthz.status_code}")
        print(f"/readyz -> {readyz.status_code}")

        if not using_existing_user:
            _step("Register throwaway user")
            payload = {"email": email, "full_name": full_name, "password": password}
            _request(
                client,
                "POST",
                _api_url(ctx, "/auth/register"),
                step_name="POST /auth/register",
                expected_status=200,
                json=payload,
                retries=ctx.retries,
                retry_delay_seconds=ctx.retry_delay_seconds,
            )
            created_user = True
            print(f"Registered throwaway user: {email}")

        _step("Login")
        login = _request(
            client,
            "POST",
            _api_url(ctx, "/auth/login"),
            step_name="POST /auth/login",
            expected_status=200,
            json={"email": email, "password": password},
            retries=ctx.retries,
            retry_delay_seconds=ctx.retry_delay_seconds,
        ).json()
        access_token = login["access_token"]
        refresh_token = login["refresh_token"]
        auth_headers = {"Authorization": f"Bearer {access_token}"}
        print("Login successful")

        _step("Refresh token")
        _request(
            client,
            "POST",
            _api_url(ctx, "/auth/refresh"),
            step_name="POST /auth/refresh",
            expected_status=200,
            json={"refresh_token": refresh_token},
            retries=ctx.retries,
            retry_delay_seconds=ctx.retry_delay_seconds,
        )
        print("Refresh successful")

        _step("Auth profile")
        me = _request(
            client,
            "GET",
            _api_url(ctx, "/auth/me"),
            step_name="GET /auth/me",
            expected_status=200,
            headers=auth_headers,
            retries=ctx.retries,
            retry_delay_seconds=ctx.retry_delay_seconds,
        ).json()
        print(f"Authenticated user: {me.get('email')}")

        _step("Wallet")
        wallet = _request(
            client,
            "GET",
            _api_url(ctx, "/wallet/me"),
            step_name="GET /wallet/me",
            expected_status=200,
            headers=auth_headers,
            retries=ctx.retries,
            retry_delay_seconds=ctx.retry_delay_seconds,
        ).json()
        print(f"Wallet balance: {wallet.get('balance')}")

        if check_wallet_fund:
            _step("Wallet funding initialization")
            _request(
                client,
                "POST",
                _api_url(ctx, "/wallet/fund"),
                step_name="POST /wallet/fund",
                expected_status=200,
                headers=auth_headers,
                json={"amount": 100, "callback_url": f"{ctx.base_url}/app/wallet"},
                retries=ctx.retries,
                retry_delay_seconds=ctx.retry_delay_seconds,
            )
            print("Wallet funding initialization passed")

        if check_forgot_password:
            _step("Forgot password")
            _request(
                client,
                "POST",
                _api_url(ctx, "/auth/forgot-password"),
                step_name="POST /auth/forgot-password",
                expected_status=200,
                json={"email": email},
                retries=ctx.retries,
                retry_delay_seconds=ctx.retry_delay_seconds,
            )
            print("Forgot password endpoint passed")

        if cleanup_created_user and created_user and access_token:
            _step("Cleanup throwaway user")
            _request(
                client,
                "DELETE",
                _api_url(ctx, "/auth/delete-me"),
                step_name="DELETE /auth/delete-me",
                expected_status=200,
                headers={"Authorization": f"Bearer {access_token}"},
                retries=ctx.retries,
                retry_delay_seconds=ctx.retry_delay_seconds,
            )
            print("Cleanup completed")

    print("\nSUCCESS: production smoke checks passed.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run AxisVTU production smoke checks.")
    parser.add_argument("--base-url", required=True, help="Backend base URL, e.g. https://your-api.onrender.com")
    parser.add_argument("--api-prefix", default="/api/v1", help="API prefix (default: /api/v1)")
    parser.add_argument("--email", default=None, help="Existing user email; if omitted, a throwaway user is created")
    parser.add_argument("--password", default=None, help="Existing user password")
    parser.add_argument("--full-name", default="AxisVTU Smoke User", help="Name for throwaway user registration")
    parser.add_argument("--timeout", type=float, default=20.0, help="HTTP timeout in seconds")
    parser.add_argument("--retries", type=int, default=3, help="Retries for transient network/5xx errors")
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=5.0,
        help="Delay between retries in seconds",
    )
    parser.add_argument("--insecure", action="store_true", help="Disable TLS certificate verification")
    parser.add_argument(
        "--skip-wallet-fund",
        action="store_true",
        help="Skip wallet funding initialization check",
    )
    parser.add_argument(
        "--skip-forgot-password",
        action="store_true",
        help="Skip forgot-password endpoint check",
    )
    parser.add_argument(
        "--keep-user",
        action="store_true",
        help="Do not delete throwaway user after test run",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    run_smoke(
        base_url=args.base_url,
        api_prefix=args.api_prefix,
        email=args.email,
        password=args.password,
        full_name=args.full_name,
        timeout_seconds=args.timeout,
        verify_tls=not args.insecure,
        retries=max(0, args.retries),
        retry_delay_seconds=max(0.0, args.retry_delay),
        check_wallet_fund=not args.skip_wallet_fund,
        check_forgot_password=not args.skip_forgot_password,
        cleanup_created_user=not args.keep_user,
    )


if __name__ == "__main__":
    main()
