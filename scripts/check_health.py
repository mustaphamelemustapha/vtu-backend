#!/usr/bin/env python3
"""Production health checks for AxisVTU backend endpoints."""

from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def fail(message: str) -> None:
    print(f"ERROR: {message}")
    raise SystemExit(1)


def load_json(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        fail("Endpoint did not return valid JSON.")
    if not isinstance(data, dict):
        fail("Endpoint returned JSON that is not an object.")
    return data


def normalize_base_url(base_url: str) -> str:
    url = (base_url or "").strip().rstrip("/")
    # Accept accidental values like ".../api" or ".../api/v1" in secrets.
    for suffix in ("/api/v1", "/api"):
        if url.endswith(suffix):
            return url[: -len(suffix)]
    return url


def _request_json(url: str, timeout: int) -> tuple[int, dict[str, Any]]:
    request = Request(url, headers={"User-Agent": "axisvtu-healthcheck/1.0"})
    with urlopen(request, timeout=timeout) as response:
        body_text = response.read().decode("utf-8", "replace")
        status_code = response.getcode()
    return status_code, load_json(body_text)


def check_endpoint(
    base_url: str,
    path: str,
    expected_status: str,
    *,
    timeout: int,
    retries: int,
    retry_delay: float,
) -> None:
    url = f"{base_url}{path}"
    last_error = None

    for attempt in range(retries + 1):
        try:
            status_code, data = _request_json(url, timeout=timeout)
            if status_code != 200:
                raise RuntimeError(f"{path} returned HTTP {status_code}, expected 200.")
            actual_status = data.get("status")
            if actual_status != expected_status:
                raise RuntimeError(
                    f"{path} status mismatch: expected '{expected_status}', got '{actual_status}'."
                )
            print(f"OK: {path} -> status={actual_status}")
            return
        except HTTPError as exc:
            payload = exc.read().decode("utf-8", "replace")
            last_error = f"{path} returned HTTP {exc.code}. Body: {payload}"
        except (URLError, TimeoutError) as exc:
            last_error = f"{path} request failed: {exc}"
        except Exception as exc:
            last_error = str(exc)

        if attempt < retries:
            wait = retry_delay * (attempt + 1)
            print(f"WARN: {last_error} (retry {attempt + 1}/{retries} in {wait:.1f}s)")
            time.sleep(wait)

    fail(last_error or f"{path} failed")


def main() -> None:
    base_url = normalize_base_url(os.getenv("PROD_BACKEND_BASE_URL", ""))
    if not base_url:
        fail("Missing PROD_BACKEND_BASE_URL environment variable.")

    timeout = int(os.getenv("HEALTHCHECK_TIMEOUT_SECONDS", "25"))
    retries = int(os.getenv("HEALTHCHECK_RETRIES", "4"))
    retry_delay = float(os.getenv("HEALTHCHECK_RETRY_DELAY_SECONDS", "4"))

    print(
        f"Healthcheck config: base_url={base_url} timeout={timeout}s retries={retries} "
        f"retry_delay={retry_delay}s"
    )

    check_endpoint(
        base_url,
        "/healthz",
        "ok",
        timeout=timeout,
        retries=retries,
        retry_delay=retry_delay,
    )
    check_endpoint(
        base_url,
        "/readyz",
        "ready",
        timeout=timeout,
        retries=retries,
        retry_delay=retry_delay,
    )
    print("SUCCESS: all health checks passed.")


if __name__ == "__main__":
    main()
