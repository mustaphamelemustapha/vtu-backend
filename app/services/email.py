from __future__ import annotations

import smtplib
from email.message import EmailMessage
from email.utils import parseaddr
from urllib.parse import urlparse
from typing import Optional

import httpx

from app.core.config import get_settings


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


def _build_reset_email_html(reset_link: str) -> str:
    # Keep HTML minimal and compatible across mail clients.
    return f"""
    <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #0f172a;">
      <h2 style="margin: 0 0 8px;">Reset your MELE DATA password</h2>
      <p style="margin: 0 0 14px;">
        We received a request to reset your password. If you did not request this, you can ignore this email.
      </p>
      <p style="margin: 0 0 16px;">
        <a href="{reset_link}" style="display:inline-block;background:#0f766e;color:#fff;padding:10px 14px;border-radius:12px;text-decoration:none;font-weight:700;">
          Reset Password
        </a>
      </p>
      <p style="margin: 0 0 6px; color: #475569; font-size: 13px;">
        Or copy and paste this link:
      </p>
      <p style="margin: 0; font-size: 13px;">
        <a href="{reset_link}" style="color:#0f766e;">{reset_link}</a>
      </p>
    </div>
    """.strip()


def _build_pin_reset_email_html(reset_link: str) -> str:
    return f"""
    <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #0f172a;">
      <h2 style="margin: 0 0 8px;">Reset your MELE DATA transaction PIN</h2>
      <p style="margin: 0 0 14px;">
        We received a request to reset your transaction PIN. If you did not request this, you can ignore this email.
      </p>
      <p style="margin: 0 0 16px;">
        <a href="{reset_link}" style="display:inline-block;background:#0f766e;color:#fff;padding:10px 14px;border-radius:12px;text-decoration:none;font-weight:700;">
          Reset Transaction PIN
        </a>
      </p>
      <p style="margin: 0 0 6px; color: #475569; font-size: 13px;">
        Or copy and paste this link:
      </p>
      <p style="margin: 0; font-size: 13px;">
        <a href="{reset_link}" style="color:#0f766e;">{reset_link}</a>
      </p>
    </div>
    """.strip()


def _resolve_frontend_base_url() -> str:
    settings = get_settings()
    raw = (settings.frontend_base_url or "").strip().rstrip("/")
    if not raw:
        return "https://axisvtu.com"

    # Accept both full URLs and bare hosts from env/config.
    candidate = raw if raw.startswith(("http://", "https://")) else f"https://{raw}"
    host = urlparse(candidate).netloc.lower()
    raw_lower = raw.lower()
    if "vercel.app" in host or "vercel.app" in raw_lower:
        return "https://axisvtu.com"
    return candidate.rstrip("/")


def send_password_reset_email(to_email: str, reset_token: str) -> None:
    settings = get_settings()
    reset_link = f"{_resolve_frontend_base_url()}/reset-password?token={reset_token}&flow=password"
    subject = "Reset your MELE DATA password"
    html = _build_reset_email_html(reset_link)
    to_email = _sanitize_email(to_email)

    provider = (settings.email_provider or "console").lower()
    if provider == "console":
        # Safe default for dev/test; shows link in logs.
        print(f"[email][console] to={to_email} subject={subject} link={reset_link}")
        return

    if provider == "resend":
        _send_via_resend(
            api_key=settings.resend_api_key,
            email_from=_sanitize_email_from(settings.email_from),
            to_email=to_email,
            subject=subject,
            html=html,
        )
        return

    if provider == "brevo":
        name, from_email = _parse_from(settings.email_from)
        _send_via_brevo(
            api_key=settings.brevo_api_key,
            from_name=name,
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
            email_from=_sanitize_email_from(settings.email_from),
            to_email=to_email,
            subject=subject,
            html=html,
        )
        return

    raise ValueError(f"Unsupported EMAIL_PROVIDER: {settings.email_provider}")


def send_transaction_pin_reset_email(to_email: str, reset_token: str) -> None:
    settings = get_settings()
    reset_link = f"{_resolve_frontend_base_url()}/reset-pin?token={reset_token}&flow=pin"
    subject = "Reset your MELE DATA transaction PIN"
    html = _build_pin_reset_email_html(reset_link)
    to_email = _sanitize_email(to_email)

    provider = (settings.email_provider or "console").lower()
    if provider == "console":
        print(f"[email][console] to={to_email} subject={subject} link={reset_link}")
        return

    if provider == "resend":
        _send_via_resend(
            api_key=settings.resend_api_key,
            email_from=_sanitize_email_from(settings.email_from),
            to_email=to_email,
            subject=subject,
            html=html,
        )
        return

    if provider == "brevo":
        name, from_email = _parse_from(settings.email_from)
        _send_via_brevo(
            api_key=settings.brevo_api_key,
            from_name=name,
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
            email_from=_sanitize_email_from(settings.email_from),
            to_email=to_email,
            subject=subject,
            html=html,
        )
        return

    raise ValueError(f"Unsupported EMAIL_PROVIDER: {settings.email_provider}")


def _send_via_resend(
    *,
    api_key: Optional[str],
    email_from: str,
    to_email: str,
    subject: str,
    html: str,
) -> None:
    if not api_key:
        raise ValueError("RESEND_API_KEY is required when EMAIL_PROVIDER=resend")

    payload = {
        "from": email_from,
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
        "sender": {"name": from_name or "MELE DATA", "email": from_email},
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
    email_from: str,
    to_email: str,
    subject: str,
    html: str,
) -> None:
    if not host:
        raise ValueError("SMTP_HOST is required when EMAIL_PROVIDER=smtp")

    msg = EmailMessage()
    msg["From"] = email_from
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


def _build_welcome_email_html(email: str, password: str, name: str) -> str:
    display_name = name if name and name.strip() else "there"
    return f"""
    <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #0f172a;">
      <h2 style="margin: 0 0 16px; color: #0f766e;">Welcome to MELE DATA! 🚀</h2>
      <p style="margin: 0 0 14px;">
        Hi {display_name},
      </p>
      <p style="margin: 0 0 14px;">
        Thank you for registering with MELE DATA. We are thrilled to have you on board! 
        You can now log in to your account and start enjoying fast, reliable, and affordable VTU services.
      </p>
      <div style="background-color: #f8fafc; padding: 16px; border-radius: 8px; border: 1px solid #e2e8f0; margin-bottom: 20px;">
        <h3 style="margin: 0 0 12px; font-size: 16px; color: #334155;">Your Login Credentials</h3>
        <p style="margin: 0 0 8px;"><strong>Email:</strong> {email}</p>
        <p style="margin: 0 0 0;"><strong>Password:</strong> <span style="font-family: monospace; background: #e2e8f0; padding: 2px 6px; border-radius: 4px;">{password}</span></p>
      </div>
      <p style="margin: 0 0 16px;">
        Please keep these credentials safe. You can log in via our mobile app or website.
      </p>
      <p style="margin: 0 0 16px;">
        <a href="{_resolve_frontend_base_url()}/login" style="display:inline-block;background:#0f766e;color:#fff;padding:12px 20px;border-radius:8px;text-decoration:none;font-weight:bold;">
          Log In Now
        </a>
      </p>
      <p style="margin: 0; font-size: 14px; color: #64748b;">
        Best regards,<br>
        The MELE DATA Team
      </p>
    </div>
    """.strip()


def send_welcome_email(to_email: str, password: str, name: str = "") -> None:
    settings = get_settings()
    subject = "Welcome to MELE DATA!"
    html = _build_welcome_email_html(to_email, password, name)
    to_email = _sanitize_email(to_email)

    provider = (settings.email_provider or "console").lower()
    if provider == "console":
        print(f"[email][console] to={to_email} subject={subject} password={password}")
        return

    if provider == "resend":
        _send_via_resend(
            api_key=settings.resend_api_key,
            email_from=_sanitize_email_from(settings.email_from),
            to_email=to_email,
            subject=subject,
            html=html,
        )
        return

    if provider == "brevo":
        from_name, from_email = _parse_from(settings.email_from)
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
            email_from=_sanitize_email_from(settings.email_from),
            to_email=to_email,
            subject=subject,
            html=html,
        )
        return

    raise ValueError(f"Unsupported EMAIL_PROVIDER: {settings.email_provider}")


def _build_low_balance_email_html(provider: str, balance: float) -> str:
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
        Please log into the {provider} dashboard and fund your wallet immediately to prevent your users from experiencing failed transactions!
      </p>
      <p style="margin: 0; font-size: 14px; color: #64748b;">
        This is an automated system alert from MELE DATA.
      </p>
    </div>
    """.strip()


def send_admin_low_balance_email(to_email: str, provider: str, balance: float) -> None:
    settings = get_settings()
    subject = f"URGENT: Low Balance on {provider} (₦{balance:,.2f})"
    html = _build_low_balance_email_html(provider, balance)
    to_email = _sanitize_email(to_email)

    email_provider = (settings.email_provider or "console").lower()
    if email_provider == "console":
        print(f"[email][console] to={to_email} subject={subject} balance={balance}")
        return

    if email_provider == "resend":
        _send_via_resend(
            api_key=settings.resend_api_key,
            email_from=_sanitize_email_from(settings.email_from),
            to_email=to_email,
            subject=subject,
            html=html,
        )
        return

    if email_provider == "brevo":
        from_name, from_email = _parse_from(settings.email_from)
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
            email_from=_sanitize_email_from(settings.email_from),
            to_email=to_email,
            subject=subject,
            html=html,
        )
        return

    raise ValueError(f"Unsupported EMAIL_PROVIDER: {settings.email_provider}")


def _build_daily_report_email_html(stats: dict) -> str:
    date_str = stats.get('date', '')
    total_sales = stats.get('total_sales', 0)
    total_funding = stats.get('total_funding', 0)
    new_users = stats.get('new_users', 0)
    pending_txs = stats.get('pending_txs', 0)

    return f"""
    <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
      <h2 style="color: #2563eb; margin-bottom: 20px;">📊 Daily Performance Report - {date_str}</h2>
      
      <p style="font-size: 16px; margin-bottom: 20px;">
        Here is the summary of your VTU platform's performance for today.
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
        This is an automated system report from MELE DATA. Keep up the great work!
      </p>
    </div>
    """.strip()


def send_admin_daily_report_email(to_email: str, stats: dict) -> None:
    settings = get_settings()
    date_str = stats.get('date', '')
    subject = f"Daily Report: ₦{stats.get('total_sales', 0):,.2f} Sales on {date_str}"
    html = _build_daily_report_email_html(stats)
    to_email = _sanitize_email(to_email)

    email_provider = (settings.email_provider or "console").lower()
    if email_provider == "console":
        print(f"[email][console] to={to_email} subject={subject}")
        return

    if email_provider == "resend":
        _send_via_resend(
            api_key=settings.resend_api_key,
            email_from=_sanitize_email_from(settings.email_from),
            to_email=to_email,
            subject=subject,
            html=html,
        )
        return

    if email_provider == "brevo":
        from_name, from_email = _parse_from(settings.email_from)
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
            email_from=_sanitize_email_from(settings.email_from),
            to_email=to_email,
            subject=subject,
            html=html,
        )
        return

    raise ValueError(f"Unsupported EMAIL_PROVIDER: {settings.email_provider}")
