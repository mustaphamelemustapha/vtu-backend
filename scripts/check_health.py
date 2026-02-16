#!/usr/bin/env python3
"""Production health checks for AxisVTU backend endpoints."""

from __future__ import annotations

import json
import os
import sys
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


def check_endpoint(base_url: str, path: str, expected_status: str) -> None:
    url = f"{base_url}{path}"
    request = Request(url, headers={"User-Agent": "axisvtu-healthcheck/1.0"})

    try:
        with urlopen(request, timeout=20) as response:
            body_text = response.read().decode("utf-8", "replace")
            status_code = response.getcode()
    except HTTPError as exc:
        payload = exc.read().decode("utf-8", "replace")
        fail(f"{path} returned HTTP {exc.code}. Body: {payload}")
    except URLError as exc:
        fail(f"{path} request failed: {exc.reason}")
    except TimeoutError:
        fail(f"{path} request timed out.")

    if status_code != 200:
        fail(f"{path} returned HTTP {status_code}, expected 200.")

    data = load_json(body_text)
    actual_status = data.get("status")
    if actual_status != expected_status:
        fail(f"{path} status mismatch: expected '{expected_status}', got '{actual_status}'.")

    print(f"OK: {path} -> status={actual_status}")


def main() -> None:
    base_url = os.getenv("PROD_BACKEND_BASE_URL", "").strip().rstrip("/")
    if not base_url:
        fail("Missing PROD_BACKEND_BASE_URL environment variable.")

    check_endpoint(base_url, "/healthz", "ok")
    check_endpoint(base_url, "/readyz", "ready")
    print("SUCCESS: all health checks passed.")


if __name__ == "__main__":
    main()
