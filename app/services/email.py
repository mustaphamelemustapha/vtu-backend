from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from email.utils import parseaddr
from urllib.parse import urlparse
from typing import Optional, Dict, Any

import httpx

from app.core.config import get_settings


BRAND_CONFIGS: Dict[str, Dict[str, Any]] = {
    "meledata": {
        "key": "meledata",
        "name": "MELE DATA",
        "short_name": "Mele Data",
        "domain": "meledata.ng",
        "url": "https://meledata.ng",
        "support_email": "mmtechglobe@gmail.com",
        "support_phone": "+234 814 111 4647",
        "primary_color": "#0B4FD2",
        "accent_color": "#00C6FF",
        "logo_url": "https://meledata.ng/brand/meledata-logo.png",
    },
    "kullomadata": {
        "key": "kullomadata",
        "name": "KULLOMA DATA",
        "short_name": "Kulloma Data",
        "domain": "kullomadata.com",
        "url": "https://www.kullomadata.com",
        "support_email": "kullomadata@gmail.com",
        "support_phone": "+234 814 111 4647",
        "primary_color": "#5B21B6",
        "accent_color": "#F59E0B",
        "logo_url": "https://www.kullomadata.com/brand/kullomadata-logo.png",
    },
    "mzdata": {
        "key": "mzdata",
        "name": "MZ DATA",
        "short_name": "MZ Data",
        "domain": "mzdata.org",
        "url": "https://www.mzdata.org",
        "support_email": "mzdatamzdata2@gmail.com",
        "support_phone": "09067054672",
        "primary_color": "#0A2540",
        "accent_color": "#10B981",
        "logo_url": "https://www.mzdata.org/brand/mzdata-logo.jpg",
    },
}


def resolve_brand_meta(brand: Optional[str] = None) -> Dict[str, Any]:
    """
    Resolves the brand metadata (name, domain, colors, support info) dynamically.
    Priority:
    1. Explicit brand parameter (e.g. from user.brand or caller)
    2. Environment variables: BRAND_NAME, APP_NAME, settings.app_name
    3. Configured frontend_base_url
    4. Default to 'meledata'
    """
    if brand:
        b = str(brand).strip().lower()
        if "mele" in b:
            return BRAND_CONFIGS["meledata"]
        if "mz" in b:
            return BRAND_CONFIGS["mzdata"]
        if "kulloma" in b:
            return BRAND_CONFIGS["kullomadata"]
        if b in BRAND_CONFIGS:
            return BRAND_CONFIGS[b]

    settings = get_settings()
    env_brand = (
        os.environ.get("BRAND_NAME")
        or os.environ.get("APP_NAME")
        or getattr(settings, "app_name", "")
        or ""
    ).strip().lower()

    if "mele" in env_brand:
        return BRAND_CONFIGS["meledata"]
    if "mz" in env_brand:
        return BRAND_CONFIGS["mzdata"]
    if "kulloma" in env_brand:
        return BRAND_CONFIGS["kullomadata"]

    raw_base = (settings.frontend_base_url or "").lower()
    if "kulloma" in raw_base:
        return BRAND_CONFIGS["kullomadata"]
    if "mz" in raw_base:
        return BRAND_CONFIGS["mzdata"]

    return BRAND_CONFIGS["meledata"]


def _sanitize_email_from(value: str) -> str:
    # Render env vars are often pasted with surrounding quotes; Resend rejects that.
    v = (value or "").strip()
    if len(v) >= 2 and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
        v = v[1:-1].strip()
    return v


def _sanitize_email(value: str) -> str:
    return (value or "").strip()


def _parse_from(value: str) -> tuple[Optional[str], str]:
    """
    Accept either:
      - email@example.com
      - Name <email@example.com>
    Returns (name, email).
    """
    raw = _sanitize_email_from(value)
    name, email = parseaddr(raw)
    name = (name or "").strip() or None
    email = (email or "").strip()
    # If parseaddr fails (malformed input), fall back to the raw string.
    if not email:
        return None, raw.strip()
    return name, email


def get_brand_from_address(brand_meta: Dict[str, Any]) -> tuple[str, str]:
    """
    Returns (from_name, from_email) ensuring the display name matches the brand,
    while retaining a valid verified sender email for delivery.
    """
    settings = get_settings()
    key = brand_meta.get("key", "meledata")

    # 1. Check brand-specific environment override (e.g. EMAIL_FROM_MELEDATA)
    env_override = os.environ.get(f"EMAIL_FROM_{key.upper()}")
    if env_override:
        name, email = _parse_from(env_override)
        if email:
            return (name or brand_meta["name"], email)

    # 2. Extract verified sender email address from settings.email_from
    name, email = _parse_from(settings.email_from)
    from_email = email or f"no-reply@{brand_meta.get('domain', 'meledata.ng')}"
    from_name = brand_meta["name"]
    return (from_name, from_email)


def _resolve_frontend_base_url(brand_meta: Optional[Dict[str, Any]] = None) -> str:
    meta = brand_meta or BRAND_CONFIGS["meledata"]
    settings = get_settings()
    env = (settings.environment or "").lower()
    raw = (settings.frontend_base_url or "").strip().rstrip("/")
    if env == "development" and raw and ("localhost" in raw or "127.0.0.1" in raw):
        return raw

    return meta.get("url") or f"https://{meta.get('domain', 'meledata.ng')}"


def _get_base_email_template(
    title: str,
    preheader: str,
    content: str,
    action_url: str,
    action_text: str,
    brand_meta: Dict[str, Any],
) -> str:
    brand_name = brand_meta.get("name", "MELE DATA")
    primary_color = brand_meta.get("primary_color", "#0B4FD2")
    support_email = brand_meta.get("support_email", "mmtechglobe@gmail.com")

    btn_html = ""
    if action_url and action_text:
        btn_html = f"""
        <div style="margin: 30px 0 20px; text-align: center;">
          <a href="{action_url}" style="display: inline-block; background-color: {primary_color}; color: #ffffff !important; padding: 14px 34px; border-radius: 10px; text-decoration: none; font-weight: 700; font-size: 15px; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);">{action_text}</a>
        </div>
        <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px 16px; margin-top: 24px;">
          <p style="font-size: 12px; color: #64748b; margin: 0 0 6px;">If the button above doesn't work, copy and paste this link into your browser:</p>
          <p style="word-break: break-all; margin: 0; font-size: 12px;"><a href="{action_url}" style="color: {primary_color}; text-decoration: none;">{action_url}</a></p>
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f1f5f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; -webkit-font-smoothing: antialiased;">
  <div style="display:none;font-size:1px;color:#f8fafc;line-height:1px;max-height:0px;max-width:0px;opacity:0;overflow:hidden;">
    {preheader}
  </div>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: #f1f5f9; padding: 36px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" style="max-width: 580px; background-color: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 4px 20px -2px rgba(15, 23, 42, 0.08); border: 1px solid #e2e8f0;" cellspacing="0" cellpadding="0">
          <!-- Header -->
          <tr>
            <td style="background-color: {primary_color}; padding: 32px 36px; text-align: center;">
              <h1 style="margin: 0; color: #ffffff; font-size: 24px; font-weight: 800; letter-spacing: 0.5px; text-transform: uppercase;">{brand_name}</h1>
              <p style="margin: 6px 0 0; color: rgba(255, 255, 255, 0.9); font-size: 14px; font-weight: 500;">{title}</p>
            </td>
          </tr>
          <!-- Content Body -->
          <tr>
            <td style="padding: 36px 36px 28px; color: #334155; line-height: 1.6; font-size: 15px;">
              {content}
              {btn_html}
            </td>
          </tr>
          <!-- Footer -->
          <tr>
            <td style="padding: 24px 36px; background-color: #f8fafc; text-align: center; color: #64748b; font-size: 13px; border-top: 1px solid #e2e8f0;">
              <p style="margin: 0 0 6px 0; font-weight: 500;">© 2026 {brand_name}. All rights reserved.</p>
              <p style="margin: 0;">Need help? <a href="mailto:{support_email}" style="color: {primary_color}; text-decoration: none; font-weight: 600;">Contact Support ({support_email})</a></p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>""".strip()


def _build_reset_email_html(reset_link: str, brand_meta: Dict[str, Any]) -> str:
    brand_name = brand_meta["name"]
    content = f"""
    <p style="margin: 0 0 16px; font-size: 16px;">Hello,</p>
    <p style="margin: 0 0 20px; line-height: 1.6;">
      We received a request to reset your password for your <strong>{brand_name}</strong> account.
      Click the button below to securely choose a new password:
    </p>
    <p style="margin: 0; font-size: 14px; color: #64748b;">
      If you did not request a password reset, you can safely ignore this email. Your password will remain unchanged.
    </p>
    """
    return _get_base_email_template(
        title="Reset your Password",
        preheader=f"Instructions for resetting your {brand_name} password.",
        content=content,
        action_url=reset_link,
        action_text="Reset Password",
        brand_meta=brand_meta,
    )


def _build_otp_email_html(otp: str, brand_meta: Dict[str, Any]) -> str:
    brand_name = brand_meta["name"]
    primary_color = brand_meta["primary_color"]
    content = f"""
    <p style="margin: 0 0 16px; font-size: 16px;">Hello,</p>
    <p style="margin: 0 0 20px; line-height: 1.6;">
      We received a request to reset your <strong>{brand_name}</strong> account password.
      Please enter the 6-digit verification code below to proceed:
    </p>
    <div style="text-align: center; margin: 28px 0; padding: 20px; background-color: #f8fafc; border: 2px dashed {primary_color}; border-radius: 12px;">
      <div style="font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 1.5px; color: #64748b; margin-bottom: 6px;">Your One-Time Password (OTP)</div>
      <div style="font-size: 36px; font-weight: 800; letter-spacing: 8px; color: {primary_color}; font-family: monospace;">{otp}</div>
    </div>
    <p style="margin: 0 0 12px; font-size: 14px; color: #64748b;">
      ⏰ This verification code expires in <strong>15 minutes</strong> for security.
    </p>
    <p style="margin: 0; font-size: 14px; color: #64748b;">
      If you did not initiate this request, you can safely ignore this email. Your account remains completely secure.
    </p>
    """
    return _get_base_email_template(
        title="Password Reset Verification Code",
        preheader=f"Your {brand_name} password reset verification code is {otp}",
        content=content,
        action_url=brand_meta["url"],
        action_text=f"Open {brand_name}",
        brand_meta=brand_meta,
    )


def _build_pin_reset_email_html(reset_link: str, brand_meta: Dict[str, Any]) -> str:
    brand_name = brand_meta["name"]
    content = f"""
    <p style="margin: 0 0 16px; font-size: 16px;">Hello,</p>
    <p style="margin: 0 0 20px; line-height: 1.6;">
      We received a request to reset your 4-digit transaction PIN for your <strong>{brand_name}</strong> account.
      Click the button below to securely set a new transaction PIN:
    </p>
    <p style="margin: 20px 0 0; font-size: 14px; color: #dc2626; font-weight: 500;">
      ⚠️ Never share your transaction PIN or this reset link with anyone.
    </p>
    """
    return _get_base_email_template(
        title="Reset your Transaction PIN",
        preheader=f"Instructions for resetting your {brand_name} transaction PIN.",
        content=content,
        action_url=reset_link,
        action_text="Reset Transaction PIN",
        brand_meta=brand_meta,
    )


def _build_welcome_email_html(email: str, password: str, name: str, brand_meta: Dict[str, Any]) -> str:
    brand_name = brand_meta["name"]
    display_name = name if name and name.strip() else "there"
    login_url = f"{brand_meta['url']}/login"

    content = f"""
    <p style="margin: 0 0 16px; font-size: 16px;">Hi {display_name},</p>
    <p style="margin: 0 0 18px; line-height: 1.6;">
      Thank you for registering with <strong>{brand_name}</strong>! We are thrilled to have you on board.
      You can now log in to your account and start enjoying fast, reliable, and affordable VTU airtime, data bundles, electricity tokens, and bills payment.
    </p>
    <div style="background-color: #f8fafc; padding: 20px; border-radius: 12px; border: 1px solid #e2e8f0; margin: 24px 0;">
      <h3 style="margin: 0 0 14px; font-size: 14px; color: #334155; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;">Your Account Credentials</h3>
      <p style="margin: 0 0 10px; font-size: 15px;"><strong>Email:</strong> <span style="color: #0f172a;">{email}</span></p>
      <p style="margin: 0; font-size: 15px;"><strong>Password:</strong> <span style="font-family: monospace; background: #e2e8f0; padding: 3px 8px; border-radius: 6px; font-size: 15px; color: #0f172a;">{password}</span></p>
    </div>
    <p style="margin: 0 0 20px; font-size: 14px; color: #64748b;">
      Please keep these credentials safe. For security, you can change your password anytime after logging in.
    </p>
    """
    return _get_base_email_template(
        title=f"Welcome to {brand_name}! 🚀",
        preheader=f"Welcome to {brand_name}! Your login credentials inside.",
        content=content,
        action_url=login_url,
        action_text="Log In Now",
        brand_meta=brand_meta,
    )


def _build_low_balance_email_html(provider: str, balance: float, brand_meta: Dict[str, Any]) -> str:
    brand_name = brand_meta["name"]
    return f"""
    <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #0f172a;">
      <h2 style="margin: 0 0 16px; color: #dc2626;">🚨 Low Balance Alert: {provider}</h2>
      <p style="margin: 0 0 14px; font-size: 16px;">
        Your <strong>{provider}</strong> API wallet balance has dropped to critically low levels.
      </p>
      <div style="background-color: #fef2f2; padding: 20px; border-radius: 8px; border: 1px solid #f87171; margin-bottom: 20px; text-align: center;">
        <h3 style="margin: 0 0 8px; font-size: 14px; color: #991b1b; text-transform: uppercase; letter-spacing: 1px;">Current Balance</h3>
        <p style="margin: 0; font-size: 32px; font-weight: bold; color: #b91c1c;">₦{balance:,.2f}</p>
      </div>
      <p style="margin: 0 0 16px; font-weight: bold;">
        Please log into the {provider} dashboard and fund your wallet immediately to prevent users from experiencing failed transactions!
      </p>
      <p style="margin: 0; font-size: 14px; color: #64748b;">
        This is an automated system alert from {brand_name}.
      </p>
    </div>
    """.strip()


def _build_daily_report_email_html(stats: dict, brand_meta: Dict[str, Any]) -> str:
    brand_name = brand_meta["name"]
    date_str = stats.get('date', '')
    total_sales = stats.get('total_sales', 0)
    total_funding = stats.get('total_funding', 0)
    new_users = stats.get('new_users', 0)
    pending_txs = stats.get('pending_txs', 0)

    return f"""
    <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
      <h2 style="color: #2563eb; margin-bottom: 20px;">📊 Daily Performance Report - {date_str}</h2>
      
      <p style="font-size: 16px; margin-bottom: 20px;">
        Here is the summary of your {brand_name} VTU platform performance for today.
      </p>

      <table style="width: 100%; border-collapse: collapse; margin-bottom: 24px;">
        <tr>
          <td style="padding: 12px; border-bottom: 1px solid #e2e8f0; font-weight: bold; width: 60%;">Total Successful Sales</td>
          <td style="padding: 12px; border-bottom: 1px solid #e2e8f0; color: #15803d; font-weight: bold; text-align: right;">₦{total_sales:,.2f}</td>
        </tr>
        <tr>
          <td style="padding: 12px; border-bottom: 1px solid #e2e8f0; font-weight: bold;">Total Wallet Deposits</td>
          <td style="padding: 12px; border-bottom: 1px solid #e2e8f0; color: #15803d; font-weight: bold; text-align: right;">₦{total_funding:,.2f}</td>
        </tr>
        <tr>
          <td style="padding: 12px; border-bottom: 1px solid #e2e8f0; font-weight: bold;">New Registered Users</td>
          <td style="padding: 12px; border-bottom: 1px solid #e2e8f0; font-weight: bold; text-align: right;">{new_users}</td>
        </tr>
        <tr>
          <td style="padding: 12px; border-bottom: 1px solid #e2e8f0; font-weight: bold; color: #b91c1c;">Pending Transactions</td>
          <td style="padding: 12px; border-bottom: 1px solid #e2e8f0; font-weight: bold; color: #b91c1c; text-align: right;">{pending_txs}</td>
        </tr>
      </table>

      <p style="font-size: 14px; color: #64748b; margin-top: 30px;">
        This is an automated system report from {brand_name}. Keep up the great work!
      </p>
    </div>
    """.strip()


def send_password_reset_email(to_email: str, reset_token: str, brand: Optional[str] = None) -> None:
    settings = get_settings()
    brand_meta = resolve_brand_meta(brand)
    from_name, from_email = get_brand_from_address(brand_meta)

    base_url = _resolve_frontend_base_url(brand_meta)
    reset_link = f"{base_url}/reset-password?token={reset_token}&flow=password"
    subject = f"Reset your {brand_meta['name']} password"
    html = _build_reset_email_html(reset_link, brand_meta)
    to_email = _sanitize_email(to_email)

    provider = (settings.email_provider or "console").lower()
    if provider == "console":
        print(f"[email][console] brand={brand_meta['name']} from='{from_name} <{from_email}>' to={to_email} subject={subject} link={reset_link}")
        return

    if provider == "resend":
        _send_via_resend(
            api_key=settings.resend_api_key,
            from_name=from_name,
            from_email=from_email,
            to_email=to_email,
            subject=subject,
            html=html,
        )
        return

    if provider == "brevo":
        _send_via_brevo(
            api_key=settings.brevo_api_key,
            from_name=from_name,
            from_email=from_email,
            to_email=to_email,
            subject=subject,
            html=html,
        )
        return

    if provider == "smtp":
        _send_via_smtp(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            use_tls=settings.smtp_use_tls,
            from_name=from_name,
            from_email=from_email,
            to_email=to_email,
            subject=subject,
            html=html,
        )
        return

    raise ValueError(f"Unsupported EMAIL_PROVIDER: {settings.email_provider}")


def send_password_reset_otp_email(to_email: str, otp: str, brand: Optional[str] = None) -> None:
    settings = get_settings()
    brand_meta = resolve_brand_meta(brand)
    from_name, from_email = get_brand_from_address(brand_meta)

    subject = f"Your {brand_meta['name']} password reset code is {otp}"
    html = _build_otp_email_html(otp, brand_meta)
    to_email = _sanitize_email(to_email)

    provider = (settings.email_provider or "console").lower()
    if provider == "console":
        print(f"[email][console] brand={brand_meta['name']} from='{from_name} <{from_email}>' to={to_email} subject={subject} otp={otp}")
        return

    if provider == "resend":
        _send_via_resend(
            api_key=settings.resend_api_key,
            from_name=from_name,
            from_email=from_email,
            to_email=to_email,
            subject=subject,
            html=html,
        )
        return

    if provider == "brevo":
        _send_via_brevo(
            api_key=settings.brevo_api_key,
            from_name=from_name,
            from_email=from_email,
            to_email=to_email,
            subject=subject,
            html=html,
        )
        return

    if provider == "smtp":
        _send_via_smtp(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            use_tls=settings.smtp_use_tls,
            from_name=from_name,
            from_email=from_email,
            to_email=to_email,
            subject=subject,
            html=html,
        )
        return

    raise ValueError(f"Unsupported EMAIL_PROVIDER: {settings.email_provider}")


def send_transaction_pin_reset_email(to_email: str, reset_token: str, brand: Optional[str] = None) -> None:
    settings = get_settings()
    brand_meta = resolve_brand_meta(brand)
    from_name, from_email = get_brand_from_address(brand_meta)

    base_url = _resolve_frontend_base_url(brand_meta)
    reset_link = f"{base_url}/reset-pin?token={reset_token}&flow=pin"
    subject = f"Reset your {brand_meta['name']} transaction PIN"
    html = _build_pin_reset_email_html(reset_link, brand_meta)
    to_email = _sanitize_email(to_email)

    provider = (settings.email_provider or "console").lower()
    if provider == "console":
        print(f"[email][console] brand={brand_meta['name']} from='{from_name} <{from_email}>' to={to_email} subject={subject} link={reset_link}")
        return

    if provider == "resend":
        _send_via_resend(
            api_key=settings.resend_api_key,
            from_name=from_name,
            from_email=from_email,
            to_email=to_email,
            subject=subject,
            html=html,
        )
        return

    if provider == "brevo":
        _send_via_brevo(
            api_key=settings.brevo_api_key,
            from_name=from_name,
            from_email=from_email,
            to_email=to_email,
            subject=subject,
            html=html,
        )
        return

    if provider == "smtp":
        _send_via_smtp(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            use_tls=settings.smtp_use_tls,
            from_name=from_name,
            from_email=from_email,
            to_email=to_email,
            subject=subject,
            html=html,
        )
        return

    raise ValueError(f"Unsupported EMAIL_PROVIDER: {settings.email_provider}")


def send_welcome_email(to_email: str, password: str, name: str = "", brand: Optional[str] = None) -> None:
    settings = get_settings()
    brand_meta = resolve_brand_meta(brand)
    from_name, from_email = get_brand_from_address(brand_meta)

    subject = f"Welcome to {brand_meta['name']}! 🚀"
    html = _build_welcome_email_html(to_email, password, name, brand_meta)
    to_email = _sanitize_email(to_email)

    provider = (settings.email_provider or "console").lower()
    if provider == "console":
        print(f"[email][console] brand={brand_meta['name']} from='{from_name} <{from_email}>' to={to_email} subject={subject} password={password}")
        return

    if provider == "resend":
        _send_via_resend(
            api_key=settings.resend_api_key,
            from_name=from_name,
            from_email=from_email,
            to_email=to_email,
            subject=subject,
            html=html,
        )
        return

    if provider == "brevo":
        _send_via_brevo(
            api_key=settings.brevo_api_key,
            from_name=from_name,
            from_email=from_email,
            to_email=to_email,
            subject=subject,
            html=html,
        )
        return

    if provider == "smtp":
        _send_via_smtp(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            use_tls=settings.smtp_use_tls,
            from_name=from_name,
            from_email=from_email,
            to_email=to_email,
            subject=subject,
            html=html,
        )
        return

    raise ValueError(f"Unsupported EMAIL_PROVIDER: {settings.email_provider}")


def _send_via_resend(
    *,
    api_key: Optional[str],
    from_name: str,
    from_email: str,
    to_email: str,
    subject: str,
    html: str,
) -> None:
    if not api_key:
        raise ValueError("RESEND_API_KEY is required when EMAIL_PROVIDER=resend")

    payload = {
        "from": f"{from_name} <{from_email}>",
        "to": [to_email],
        "subject": subject,
        "html": html,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=15) as client:
        res = client.post("https://api.resend.com/emails", json=payload, headers=headers)
        if res.status_code >= 400:
            raise RuntimeError(f"Resend error: {res.status_code} {res.text}")


def _send_via_brevo(
    *,
    api_key: Optional[str],
    from_name: Optional[str],
    from_email: str,
    to_email: str,
    subject: str,
    html: str,
) -> None:
    if not api_key:
        raise ValueError("BREVO_API_KEY is required when EMAIL_PROVIDER=brevo")
    if not from_email:
        raise ValueError("EMAIL_FROM is required when EMAIL_PROVIDER=brevo")

    payload = {
        "sender": {"name": from_name, "email": from_email},
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": html,
    }
    headers = {"api-key": api_key, "Content-Type": "application/json", "Accept": "application/json"}
    with httpx.Client(timeout=15) as client:
        res = client.post("https://api.brevo.com/v3/smtp/email", json=payload, headers=headers)
        if res.status_code >= 400:
            raise RuntimeError(f"Brevo error: {res.status_code} {res.text}")


def _send_via_smtp(
    *,
    host: Optional[str],
    port: int,
    username: Optional[str],
    password: Optional[str],
    use_tls: bool,
    from_name: str,
    from_email: str,
    to_email: str,
    subject: str,
    html: str,
) -> None:
    if not host:
        raise ValueError("SMTP_HOST is required when EMAIL_PROVIDER=smtp")

    msg = EmailMessage()
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content("Use an HTML-capable email client to view this message.")
    msg.add_alternative(html, subtype="html")

    with smtplib.SMTP(host, port, timeout=15) as server:
        server.ehlo()
        if use_tls:
            server.starttls()
            server.ehlo()
        if username and password:
            server.login(username, password)
        server.send_message(msg)


def send_admin_low_balance_email(to_email: str, provider: str, balance: float, brand: Optional[str] = None) -> None:
    settings = get_settings()
    brand_meta = resolve_brand_meta(brand)
    from_name, from_email = get_brand_from_address(brand_meta)

    subject = f"URGENT: Low Balance on {provider} (₦{balance:,.2f})"
    html = _build_low_balance_email_html(provider, balance, brand_meta)
    to_email = _sanitize_email(to_email)

    email_provider = (settings.email_provider or "console").lower()
    if email_provider == "console":
        print(f"[email][console] brand={brand_meta['name']} to={to_email} subject={subject} balance={balance}")
        return

    if email_provider == "resend":
        _send_via_resend(
            api_key=settings.resend_api_key,
            from_name=from_name,
            from_email=from_email,
            to_email=to_email,
            subject=subject,
            html=html,
        )
        return

    if email_provider == "brevo":
        _send_via_brevo(
            api_key=settings.brevo_api_key,
            from_name=from_name,
            from_email=from_email,
            to_email=to_email,
            subject=subject,
            html=html,
        )
        return

    if email_provider == "smtp":
        _send_via_smtp(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            use_tls=settings.smtp_use_tls,
            from_name=from_name,
            from_email=from_email,
            to_email=to_email,
            subject=subject,
            html=html,
        )
        return

    raise ValueError(f"Unsupported EMAIL_PROVIDER: {settings.email_provider}")


def send_admin_daily_report_email(to_email: str, stats: dict, brand: Optional[str] = None) -> None:
    settings = get_settings()
    brand_meta = resolve_brand_meta(brand)
    from_name, from_email = get_brand_from_address(brand_meta)

    date_str = stats.get('date', '')
    subject = f"Daily Report: ₦{stats.get('total_sales', 0):,.2f} Sales on {date_str}"
    html = _build_daily_report_email_html(stats, brand_meta)
    to_email = _sanitize_email(to_email)

    email_provider = (settings.email_provider or "console").lower()
    if email_provider == "console":
        print(f"[email][console] brand={brand_meta['name']} to={to_email} subject={subject}")
        return

    if email_provider == "resend":
        _send_via_resend(
            api_key=settings.resend_api_key,
            from_name=from_name,
            from_email=from_email,
            to_email=to_email,
            subject=subject,
            html=html,
        )
        return

    if email_provider == "brevo":
        _send_via_brevo(
            api_key=settings.brevo_api_key,
            from_name=from_name,
            from_email=from_email,
            to_email=to_email,
            subject=subject,
            html=html,
        )
        return

    if email_provider == "smtp":
        _send_via_smtp(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            use_tls=settings.smtp_use_tls,
            from_name=from_name,
            from_email=from_email,
            to_email=to_email,
            subject=subject,
            html=html,
        )
        return

    raise ValueError(f"Unsupported EMAIL_PROVIDER: {settings.email_provider}")
