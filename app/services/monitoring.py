import logging
from sqlalchemy.orm import Session
from app.models.user import User, UserRole
from app.services.push_notification import PushNotificationService
from app.services.amigo import AmigoClient
from app.providers.smeplug_provider import SMEPlugProvider
from app.services.bills import ClubKonnectClient

logger = logging.getLogger(__name__)

THRESHOLD = 5000.0

def check_provider_balances(db: Session) -> dict:
    results = {}
    
    # 1. Amigo
    try:
        amigo = AmigoClient()
        am_bal = amigo.get_balance()
        results["amigo"] = am_bal
        if am_bal < THRESHOLD:
            _alert_admins(db, "Amigo", am_bal)
    except Exception as e:
        logger.error(f"Monitoring Amigo failed: {e}")

    # 2. SMEPlug
    try:
        sme = SMEPlugProvider()
        sme_bal = sme.get_balance()
        results["smeplug"] = sme_bal
        if sme_bal < THRESHOLD:
            _alert_admins(db, "SMEPlug", sme_bal)
    except Exception as e:
        logger.error(f"Monitoring SMEPlug failed: {e}")

    # 3. ClubKonnect
    try:
        ck = ClubKonnectClient()
        ck_bal = ck.get_balance()
        results["clubkonnect"] = ck_bal
        if ck_bal < THRESHOLD:
            _alert_admins(db, "ClubKonnect", ck_bal)
    except Exception as e:
        logger.error(f"Monitoring ClubKonnect failed: {e}")

    return results


def _alert_admins(db: Session, provider_name: str, balance: float):
    # Find all admins with FCM tokens
    admins = db.query(User).filter(
        User.role == UserRole.ADMIN,
        User.fcm_token.isnot(None)
    ).all()

    if not admins:
        logger.warning(f"No admins with FCM tokens to alert for {provider_name} low balance.")
        return

    title = f"Low Balance Alert: {provider_name}"
    body = f"Your {provider_name} wallet balance has dropped to ₦{balance:,.2f}. Please fund it immediately to avoid transaction failures."
    
    for admin in admins:
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
