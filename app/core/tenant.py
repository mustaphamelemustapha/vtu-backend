import os
from typing import Optional
from fastapi import Request
from app.core.config import get_settings

settings = get_settings()

def get_tenant_brand(request: Optional[Request] = None) -> str:
    """
    Identifies the active tenant brand identifier (e.g. 'meledata', 'mzdata', 'kullomadata').
    Priority:
    1. Direct header: X-App-Brand, X-Brand, X-App
    2. Host / Domain name: api.meledata.ng -> meledata, mzdata.org -> mzdata, etc.
    3. Origin / Referer header
    4. Environment configuration: BRAND_NAME, APP_NAME, settings.app_name
    """
    if request is not None:
        # 1. Explicit request header
        for hdr in ("x-app-brand", "x-brand", "x-app"):
            val = request.headers.get(hdr)
            if val:
                clean = val.strip().lower()
                if "mele" in clean:
                    return "meledata"
                if "mz" in clean:
                    return "mzdata"
                if "kulloma" in clean:
                    return "kullomadata"
                return clean

        # 2. Host headers (including reverse proxy forwarding)
        for h_key in ("x-forwarded-host", "x-original-host", "host"):
            h_val = (request.headers.get(h_key) or "").lower()
            if "meledata" in h_val:
                return "meledata"
            if "mzdata" in h_val:
                return "mzdata"
            if "kulloma" in h_val:
                return "kullomadata"

        try:
            url_host = (request.url.hostname or "").lower()
            if "meledata" in url_host:
                return "meledata"
            if "mzdata" in url_host:
                return "mzdata"
            if "kulloma" in url_host:
                return "kullomadata"
        except Exception:
            pass

        # 3. Origin / Referer
        for ref_hdr in ("origin", "referer"):
            val = (request.headers.get(ref_hdr) or "").lower()
            if "meledata" in val:
                return "meledata"
            if "mzdata" in val:
                return "mzdata"
            if "kulloma" in val:
                return "kullomadata"

    # 4. Environment variables
    env_brand = (
        os.environ.get("BRAND_NAME")
        or os.environ.get("APP_NAME")
        or getattr(settings, "app_name", "")
        or ""
    ).strip().lower()

    if "mele" in env_brand:
        return "meledata"
    if "mz" in env_brand:
        return "mzdata"
    if "kulloma" in env_brand:
        return "kullomadata"

    return "default"


def is_meledata_tenant(request: Optional[Request] = None) -> bool:
    """
    Returns True if the current request or environment is scoped to MELE DATA.
    """
    return get_tenant_brand(request) == "meledata"
