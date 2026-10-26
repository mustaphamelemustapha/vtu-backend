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
            cred_dict = None
            
            for env_var in [
                "FIREBASE_CREDENTIALS_BASE64",
                "FIREBASE_CREDENTIALS_JSON",
                "FIREBASE_CREDENTIALS_KULLOMA_BASE64",
                "FIREBASE_CREDENTIALS_KULLOMA_JSON",
            ]:
                raw_creds = os.getenv(env_var)
                if not raw_creds:
                    continue
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
                
                try:
                    parsed = json.loads(raw_creds)
                    if isinstance(parsed, dict) and "private_key" in parsed:
                        cred_dict = parsed
                        break
                except Exception:
                    continue

            if cred_dict:
                cred = credentials.Certificate(cred_dict)
            else:
                cred_path = os.getenv("FIREBASE_CREDENTIALS_PATH", "firebase-adminsdk.json")
                potential_paths = [
                    cred_path,
                    os.path.join(os.getcwd(), "firebase-adminsdk.json"),
                    os.path.join(os.getcwd(), "firebase-adminsdk-kullomadata.json"),
                    "/app/firebase-adminsdk.json",
                    "/app/firebase-adminsdk-kullomadata.json",
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
                        "Firebase credentials not found! Checked env vars FIREBASE_CREDENTIALS_BASE64, "
                        "FIREBASE_CREDENTIALS_JSON and file paths. Push notifications disabled."
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
    def get_status(cls) -> dict:
        cls._initialize()
        app = None
        try:
            app = firebase_admin.get_app()
        except Exception:
            pass
        return {
            "initialized": cls._initialized,
            "has_app": app is not None,
            "project_id": app.project_id if app else None,
        }

    @classmethod
    def send_broadcast(cls, title: str, body: str, data: dict = None, image_url: str = None, tokens: list = None) -> bool:
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

            # 1. Send to standard broadcast topics (both current and legacy)
            topics = ["kullomadata_users", "meledata_users", "all_users"]
            for t in topics:
                try:
                    message = messaging.Message(
                        notification=messaging.Notification(title=title, body=body, image=image_url),
                        data=data or {},
                        topic=t,
                        android=android_config,
                        apns=apns_config,
                    )
                    resp = messaging.send(message)
                    logger.info(f"FCM broadcast to topic '{t}' succeeded: {resp}")
                except Exception as top_err:
                    logger.warning(f"FCM broadcast to topic '{t}' skipped/failed: {top_err}")

            # 2. Also send directly to device tokens via multicast (bypasses topic subscription delays)
            if tokens:
                unique_tokens = list({str(tok).strip() for tok in tokens if tok and str(tok).strip()})
                for i in range(0, len(unique_tokens), 500):
                    batch = unique_tokens[i:i+500]
                    try:
                        multicast_msg = messaging.MulticastMessage(
                            notification=messaging.Notification(title=title, body=body, image=image_url),
                            data=data or {},
                            tokens=batch,
                            android=android_config,
                            apns=apns_config,
                        )
                        batch_res = messaging.send_each_for_multicast(multicast_msg)
                        logger.info(f"FCM multicast batch sent: {batch_res.success_count} success, {batch_res.failure_count} failures.")
                    except Exception as multi_err:
                        logger.warning(f"FCM multicast batch failed: {multi_err}")

            return True
        except Exception as e:
            logger.error(f"Error sending FCM broadcast: {e}")
            return False
