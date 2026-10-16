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
            import base64
            cred = None
            raw_creds = os.getenv("FIREBASE_CREDENTIALS_JSON") or os.getenv("FIREBASE_CREDENTIALS_BASE64")
            if raw_creds:
                raw_creds = raw_creds.strip()
                if (raw_creds.startswith("'") and raw_creds.endswith("'")) or (raw_creds.startswith('"') and raw_creds.endswith('"')):
                    raw_creds = raw_creds[1:-1].strip()
                
                # Check if base64 encoded
                if not raw_creds.startswith("{"):
                    try:
                        decoded = base64.b64decode(raw_creds).decode("utf-8").strip()
                        if decoded.startswith("{"):
                            raw_creds = decoded
                    except Exception:
                        pass
                
                cred_dict = json.loads(raw_creds)
                cred = credentials.Certificate(cred_dict)
            else:
                cred_path = os.getenv("FIREBASE_CREDENTIALS_PATH", "firebase-adminsdk.json")
                potential_paths = [
                    cred_path,
                    os.path.join(os.getcwd(), "firebase-adminsdk.json"),
                    "/app/firebase-adminsdk.json",
                    "/etc/secrets/firebase-adminsdk.json",
                ]
                found_path = None
                for p in potential_paths:
                    if p and os.path.exists(p):
                        found_path = p
                        break
                
                if found_path:
                    cred = credentials.Certificate(found_path)
                else:
                    logger.warning(
                        "Firebase credentials not found! Checked env vars FIREBASE_CREDENTIALS_JSON, "
                        "FIREBASE_CREDENTIALS_BASE64 and paths. Push notifications disabled."
                    )
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
    def send_push_notification(cls, token: str, title: str, body: str, data: dict = None, sound_type: str = "default") -> bool:
        """Compatibility alias for webhooks and services calling send_push_notification."""
        return cls.send_to_token(token=token, title=title, body=body, data=data, sound_type=sound_type)

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
    def send_broadcast(cls, title: str, body: str, data: dict = None, image_url: str = None) -> bool:
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
            
            apns_payload = messaging.APNSPayload(
                aps=messaging.Aps(
                    sound="default",
                    badge=1,
                    content_available=True,
                    mutable_content=True if image_url else False,
                )
            )
            
            apns_config = messaging.APNSConfig(
                payload=apns_payload,
                fcm_options=messaging.APNSFCMOptions(image=image_url) if image_url else None
            )

            message = messaging.Message(
                notification=messaging.Notification(title=title, body=body, image=image_url),
                data=data or {},
                topic="meledata_users",
                android=android_config,
                apns=apns_config,
            )
            response = messaging.send(message)
            logger.info(f"Successfully sent FCM broadcast: {response}")
            return True
        except Exception as e:
            logger.error(f"Error sending FCM broadcast: {e}")
            return False
