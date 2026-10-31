import logging
import time
import requests
from sqlalchemy.orm import Session
from app.models.user import User, UserRole
from app.models.system_setting import SystemSetting
from app.models.integration import IntegrationProvider
from app.services.push_notification import PushNotificationService
from app.services.amigo import AmigoClient
from app.providers.smeplug_provider import SMEPlugProvider
from app.services.bills import ClubKonnectBillsProvider
from app.services.email import send_admin_low_balance_email

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 5000.0
ALERT_COOLDOWN_SECONDS = 7200  # 2 hours between repeated low-balance alerts for the same provider


def _fetch_single_provider_balance(db: Session, prov: IntegrationProvider) -> float | str:
    identifier = (prov.identifier or "").strip().lower()
    base_url = (prov.base_url or "").strip().rstrip("/")
    api_key = (prov.api_key or "").strip()

    # 1. SMEPlug
    if identifier == "smeplug":
        if api_key:
            url_candidates = [
                f"{base_url}/account/balance" if base_url else "https://smeplug.ng/api/v1/account/balance",
                f"{base_url}/wallet/balance" if base_url else "https://smeplug.ng/api/v1/wallet/balance",
            ]
            headers = {"Authorization": f"Bearer {api_key}"}
            for url in url_candidates:
                try:
                    resp = requests.get(url, headers=headers, timeout=10)
                    if resp.status_code == 200 and "application/json" in (resp.headers.get("content-type") or "").lower():
                        data = resp.json()
                        if isinstance(data, dict):
                            bal = data.get("data", {}).get("balance") if isinstance(data.get("data"), dict) else data.get("balance")
                            if bal is not None:
                                return float(bal)
                except Exception:
                    continue
        try:
            sme = SMEPlugProvider(db=db)
            sme_bal = sme.get_balance()
            if isinstance(sme_bal, (int, float)):
                return float(sme_bal)
            return str(sme_bal)
        except Exception as e:
            return f"Error: {e}"

    # 2. Amigo
    if identifier == "amigo":
        if api_key:
            url = f"{base_url}/user" if base_url else "https://amigo.ng/api/user"
            headers = {"Authorization": f"Bearer {api_key}", "Token": api_key}
            try:
                resp = requests.get(url, headers=headers, timeout=10)
                if resp.status_code == 200 and "application/json" in (resp.headers.get("content-type") or "").lower():
                    data = resp.json()
                    if isinstance(data, dict) and data.get("balance") is not None:
                        return float(data["balance"])
            except Exception:
                pass
        try:
            amigo = AmigoClient()
            am_bal = amigo.get_balance()
            if isinstance(am_bal, (int, float)):
                return float(am_bal)
            return str(am_bal)
        except Exception as e:
            return f"Error: {e}"

    # 3. ClubKonnect
    if identifier == "clubkonnect":
        try:
            ck = ClubKonnectBillsProvider()
            bal = ck.get_balance()
            if isinstance(bal, (int, float)):
                return float(bal)
            return str(bal)
        except Exception as e:
            return f"Error: {e}"

    # 4. Generic / Mele Data / MZ Data / Custom VTU Provider
    if identifier == "meledata":
        if not base_url:
            base_url = "https://api.meledata.ng/api/v1"
        for bad_host in ("https://www.meledata.ng", "https://meledata.ng", "http://www.meledata.ng", "http://meledata.ng"):
            if base_url.lower().startswith(bad_host):
                base_url = "https://api.meledata.ng" + base_url[len(bad_host):]
                break
        if api_key.startswith("mele_test_pub_"):
            api_key = "mele_pub_" + api_key[len("mele_test_pub_"):]
        elif api_key.startswith("mele_test_"):
            api_key = "mele_live_" + api_key[len("mele_test_"):]

    if not api_key:
        return "Error: Missing API key"
    if not base_url:
        return "Error: Missing Base URL"

    clean_base = base_url.rstrip("/")
    if clean_base.endswith("/developer"):
        clean_base = clean_base[:-10].rstrip("/")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-API-Key": api_key,
        "Token": api_key,
        "Accept": "application/json",
    }

    endpoints_to_try = []
    if not clean_base.endswith("/api/v1"):
        api_base = f"{clean_base}/api/v1"
        endpoints_to_try.extend([
            f"{api_base}/developer/wallet/balance",
            f"{api_base}/developer/wallet",
            f"{api_base}/developer/user",
            f"{api_base}/wallet/balance",
        ])
    endpoints_to_try.extend([
        f"{clean_base}/developer/wallet/balance",
        f"{clean_base}/developer/wallet",
        f"{clean_base}/developer/user",
        f"{clean_base}/wallet/balance",
        f"{clean_base}/account/balance",
        f"{clean_base}/balance",
        f"{clean_base}/user",
    ])

    for test_url in endpoints_to_try:
        try:
            resp = requests.get(test_url, headers=headers, timeout=10)
            if resp.status_code in (200, 201) and "application/json" in (resp.headers.get("content-type") or "").lower():
                data = resp.json()
                if isinstance(data, dict):
                    bal = None
                    if isinstance(data.get("data"), dict) and "balance" in data["data"]:
                        bal = data["data"]["balance"]
                    elif "balance" in data:
                        bal = data["balance"]
                    elif isinstance(data.get("user"), dict) and "balance" in data["user"]:
                        bal = data["user"]["balance"]
                    if bal is not None:
                        return float(bal)
        except Exception:
            continue

    return "Error: Could not fetch balance"


def check_provider_balances_detailed(db: Session) -> tuple[dict, list[dict]]:
    results: dict = {}
    providers_out: list[dict] = []

    all_providers = db.query(IntegrationProvider).order_by(IntegrationProvider.name.asc()).all()
    has_explicit_choice = any(
        isinstance(p.additional_config, dict) and "show_balance_on_dashboard" in p.additional_config
        for p in all_providers
    )

    for prov in all_providers:
        cfg = dict(prov.additional_config or {}) if isinstance(prov.additional_config, dict) else {}
        if has_explicit_choice:
            show_on_dashboard = bool(cfg.get("show_balance_on_dashboard", False))
        else:
            # Default to active providers when admin hasn't toggled specific ones yet
            show_on_dashboard = bool(prov.is_active)

        if not show_on_dashboard:
            continue

        try:
            threshold = float(cfg.get("low_balance_threshold", DEFAULT_THRESHOLD))
        except (TypeError, ValueError):
            threshold = DEFAULT_THRESHOLD

        bal_or_err = _fetch_single_provider_balance(db, prov)
        results[prov.identifier] = bal_or_err

        is_error = not isinstance(bal_or_err, (int, float))
        numeric_bal = float(bal_or_err) if not is_error else 0.0
        is_low = (not is_error) and (threshold > 0) and (numeric_bal < threshold)

        if not is_error:
            try:
                prov.balance = numeric_bal
                db.commit()
            except Exception:
                db.rollback()

            if is_low:
                _maybe_alert_low_balance(db, prov.identifier, prov.name, numeric_bal, threshold)
            else:
                # Clear low-balance cooldown once balance is healthy again
                _clear_low_alert_cooldown(db, prov.identifier)

            _check_and_update_funding(db, f"{prov.identifier}_last_balance", prov.name, numeric_bal)

        providers_out.append({
            "id": str(prov.id),
            "identifier": prov.identifier,
            "name": prov.name,
            "balance": numeric_bal if not is_error else None,
            "error": str(bal_or_err) if is_error else None,
            "low_balance_threshold": threshold,
            "is_low": is_low,
            "show_balance_on_dashboard": True,
        })

    return results, providers_out


def check_provider_balances(db: Session) -> dict:
    results, _ = check_provider_balances_detailed(db)
    return results


def _maybe_alert_low_balance(db: Session, identifier: str, provider_name: str, balance: float, threshold: float):
    cooldown_key = f"{identifier}_low_alert_ts"
    now_ts = time.time()
    setting = db.query(SystemSetting).filter(SystemSetting.key == cooldown_key).first()
    if setting:
        try:
            last_ts = float(setting.value)
            if (now_ts - last_ts) < ALERT_COOLDOWN_SECONDS:
                return
        except (TypeError, ValueError):
            pass
        setting.value = str(now_ts)
    else:
        setting = SystemSetting(key=cooldown_key, value=str(now_ts))
        db.add(setting)
    try:
        db.commit()
    except Exception:
        db.rollback()

    _alert_admins(db, provider_name, balance, alert_type="low_balance", threshold=threshold)


def _clear_low_alert_cooldown(db: Session, identifier: str):
    cooldown_key = f"{identifier}_low_alert_ts"
    setting = db.query(SystemSetting).filter(SystemSetting.key == cooldown_key).first()
    if setting and setting.value != "0":
        setting.value = "0"
        try:
            db.commit()
        except Exception:
            db.rollback()


def _check_and_update_funding(db: Session, setting_key: str, provider_name: str, current_balance: float):
    setting = db.query(SystemSetting).filter(SystemSetting.key == setting_key).first()
    if setting:
        try:
            last_balance = float(setting.value)
            # If the balance went up by at least 1000, we consider it funded
            if current_balance >= last_balance + 1000.0:
                _alert_admins(db, provider_name, current_balance, alert_type="funded")
        except ValueError:
            pass
        setting.value = str(current_balance)
    else:
        setting = SystemSetting(key=setting_key, value=str(current_balance))
        db.add(setting)

    try:
        db.commit()
    except Exception:
        db.rollback()


def _alert_admins(db: Session, provider_name: str, balance: float, alert_type: str = "low_balance", threshold: float = DEFAULT_THRESHOLD):
    admins = db.query(User).filter(User.role == UserRole.ADMIN).all()

    if not admins:
        logger.warning(f"No admins found to alert for {provider_name} {alert_type}.")
        return

    if alert_type == "funded":
        title = f"Wallet Funded: {provider_name} 🎉"
        body = f"Your {provider_name} wallet has been funded! New balance: ₦{balance:,.2f}."
    else:
        title = f"Low Balance Alert: {provider_name}"
        body = (
            f"Your {provider_name} wallet balance has dropped to ₦{balance:,.2f} "
            f"(below your ₦{threshold:,.2f} alert threshold). Please fund it immediately to avoid transaction failures."
        )

    for admin in admins:
        if admin.fcm_token:
            try:
                PushNotificationService.send_to_token(
                    token=admin.fcm_token,
                    title=title,
                    body=body,
                    data={"type": "low_balance_alert", "provider": provider_name, "threshold": str(threshold)}
                )
                logger.info(f"Sent low balance push to admin {admin.email} for {provider_name}")
            except Exception as e:
                logger.error(f"Failed to send push to admin {admin.email}: {e}")

        if alert_type == "low_balance" and admin.email:
            try:
                send_admin_low_balance_email(admin.email, provider_name, balance)
                logger.info(f"Sent low balance email to admin {admin.email} for {provider_name}")
            except Exception as e:
                logger.error(f"Failed to send email to admin {admin.email}: {e}")
