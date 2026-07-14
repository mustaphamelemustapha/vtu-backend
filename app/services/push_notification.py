import logging
import os
import firebase_admin
from firebase_admin import credentials, messaging

logger = logging.getLogger(__name__)

class PushNotificationService:
    _initialized = False

    @classmethod
    def _initialize(cls):
        if cls._initialized:
            return
        
        try:
            import json
            cred = None
            json_creds = os.getenv("FIREBASE_CREDENTIALS_JSON")
            if json_creds:
                cred_dict = json.loads(json_creds)
                cred = credentials.Certificate(cred_dict)
            else:
                cred_path = os.getenv("FIREBASE_CREDENTIALS_PATH", "firebase-adminsdk.json")
                render_secret_path = "/etc/secrets/firebase-adminsdk.json"
                
                if os.path.exists(cred_path):
                    cred = credentials.Certificate(cred_path)
                elif os.path.exists(render_secret_path):
                    cred = credentials.Certificate(render_secret_path)
                else:
                    logger.warning(f"Firebase config not found at {cred_path} or {render_secret_path}, and FIREBASE_CREDENTIALS_JSON env var not set. Push disabled.")
                    return

            firebase_admin.initialize_app(cred)
            cls._initialized = True
            logger.info("Firebase Admin initialized successfully.")
        except ValueError:
            # Already initialized
            cls._initialized = True
        except Exception as e:
            logger.error(f"Failed to initialize Firebase Admin: {e}")

    @classmethod
    def send_to_token(cls, token: str, title: str, body: str, data: dict = None, sound_type: str = "default") -> bool:
        cls._initialize()
        if not cls._initialized or not token:
            return False

        try:
            if data is None:
                data = {}
            data["sound_type"] = sound_type

            if sound_type == "balance_success":
                android_config = messaging.AndroidConfig(
                    priority="high",
                    notification=messaging.AndroidNotification(
                        sound="balance_success",
                        channel_id="custom_sound_channel",
                        click_action="FLUTTER_NOTIFICATION_CLICK",
                        default_sound=False,
                        default_vibrate_timings=True,
                    )
                )
                apns_config = messaging.APNSConfig(
                    payload=messaging.APNSPayload(
                        aps=messaging.Aps(
                            sound="balance_success.wav",
                            badge=1,
                            content_available=True,
                        )
                    )
                )
            else:
                android_config = messaging.AndroidConfig(
                    priority="high",
                    notification=messaging.AndroidNotification(
                        sound="default",
                        click_action="FLUTTER_NOTIFICATION_CLICK",
                        default_sound=True,
                        default_vibrate_timings=True,
                    )
                )
                apns_config = messaging.APNSConfig(
                    payload=messaging.APNSPayload(
                        aps=messaging.Aps(
                            sound="default",
                            badge=1,
                            content_available=True,
                        )
                    )
                )

            message = messaging.Message(
                notification=messaging.Notification(title=title, body=body),
                data=data,
                token=token,
                android=android_config,
                apns=apns_config,
            )
            response = messaging.send(message)
            logger.info(f"Successfully sent FCM message: {response}")
            return True
        except Exception as e:
            logger.error(f"Error sending FCM message: {e}")
            return False

    @classmethod
    def send_broadcast(cls, title: str, body: str, data: dict = None) -> bool:
        cls._initialize()
        if not cls._initialized:
            return False

        try:
            android_config = messaging.AndroidConfig(
                priority="high",
                notification=messaging.AndroidNotification(
                    sound="default",
                    click_action="FLUTTER_NOTIFICATION_CLICK",
                    default_sound=True,
                    default_vibrate_timings=True,
                )
            )
            apns_config = messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(
                        sound="default",
                        badge=1,
                        content_available=True,
                    )
                )
            )

            message = messaging.Message(
                notification=messaging.Notification(title=title, body=body),
                data=data or {},
                topic="all_users",
                android=android_config,
                apns=apns_config,
            )
            response = messaging.send(message)
            logger.info(f"Successfully sent FCM broadcast: {response}")
            return True
        except Exception as e:
            logger.error(f"Error sending FCM broadcast: {e}")
            return False
