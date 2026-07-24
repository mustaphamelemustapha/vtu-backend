import logging
from sqlalchemy.orm import Session
from app.models.user import User, UserRole
from app.models.system_setting import SystemSetting
from app.services.push_notification import PushNotificationService
from app.services.amigo import AmigoClient
from app.providers.smeplug_provider import SMEPlugProvider
from app.services.bills import ClubKonnectBillsProvider

logger = logging.getLogger(__name__)

THRESHOLD = 5000.0

def check_provider_balances(db: Session) -> dict:
    results = {}
    
    # 1. Amigo
    try:
        amigo = AmigoClient()
        am_bal = amigo.get_balance()
        results["amigo"] = am_bal
        if isinstance(am_bal, (int, float)):
            if am_bal < THRESHOLD:
                _alert_admins(db, "Amigo", am_bal, alert_type="low_balance")
            _check_and_update_funding(db, "amigo_last_balance", "Amigo", float(am_bal))
    except Exception as e:
        logger.error(f"Monitoring Amigo failed: {e}")
        results["amigo"] = f"Error: {e}"

    # 2. SMEPlug
    try:
        sme = SMEPlugProvider()
        sme_bal = sme.get_balance()
        results["smeplug"] = sme_bal
        if isinstance(sme_bal, (int, float)):
            if sme_bal < THRESHOLD:
                _alert_admins(db, "SMEPlug", sme_bal, alert_type="low_balance")
            _check_and_update_funding(db, "smeplug_last_balance", "SMEPlug", float(sme_bal))
    except Exception as e:
        logger.error(f"Monitoring SMEPlug failed: {e}")
        results["smeplug"] = f"Error: {e}"

    # 3. ClubKonnect
    try:
        ck = ClubKonnectBillsProvider()
        ck_bal = ck.get_balance()
        results["clubkonnect"] = ck_bal
        if isinstance(ck_bal, (int, float)):
            if ck_bal < THRESHOLD:
                _alert_admins(db, "ClubKonnect", ck_bal, alert_type="low_balance")
            _check_and_update_funding(db, "clubkonnect_last_balance", "ClubKonnect", float(ck_bal))
    except Exception as e:
        logger.error(f"Monitoring ClubKonnect failed: {e}")
        results["clubkonnect"] = f"Error: {e}"

    return results


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
    
    db.commit()


from app.services.email import send_admin_low_balance_email

def _alert_admins(db: Session, provider_name: str, balance: float, alert_type: str = "low_balance"):
    # Find all admins
    admins = db.query(User).filter(User.role == UserRole.ADMIN).all()

    if not admins:
        logger.warning(f"No admins found to alert for {provider_name} {alert_type}.")
        return

    if alert_type == "funded":
        title = f"Wallet Funded: {provider_name} 🎉"
        body = f"Your {provider_name} wallet has been funded! New balance: ₦{balance:,.2f}."
    else:
        title = f"Low Balance Alert: {provider_name}"
        body = f"Your {provider_name} wallet balance has dropped to ₦{balance:,.2f}. Please fund it immediately to avoid transaction failures."
    
    for admin in admins:
        # Send Push Notification if FCM token exists
        if admin.fcm_token:
            try:
                PushNotificationService.send_to_token(
                    token=admin.fcm_token,
                    title=title,
                    body=body,
                    data={"type": "low_balance_alert", "provider": provider_name}
                )
                logger.info(f"Sent low balance push to admin {admin.email} for {provider_name}")
            except Exception as e:
                logger.error(f"Failed to send push to admin {admin.email}: {e}")
        
        # Send Email Alert if it is a low balance alert
        if alert_type == "low_balance" and admin.email:
            try:
                send_admin_low_balance_email(admin.email, provider_name, balance)
                logger.info(f"Sent low balance email to admin {admin.email} for {provider_name}")
            except Exception as e:
                logger.error(f"Failed to send email to admin {admin.email}: {e}")
