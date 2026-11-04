import logging
import os
import firebase_admin
from firebase_admin import credentials, messaging

logger = logging.getLogger(__name__)

class PushNotificationService:
    _initialized = False
    _apps: dict = {}

    @classmethod
    def _parse_credential(cls, raw: str | None):
        if not raw:
            return None
        import json
        import base64
        raw = raw.strip()
        if (raw.startswith("'") and raw.endswith("'")) or (raw.startswith('"') and raw.endswith('"')):
            raw = raw[1:-1].strip()
        if not raw.startswith("{"):
            try:
                decoded = base64.b64decode(raw).decode("utf-8").strip()
                if decoded.startswith("{"):
                    raw = decoded
            except Exception:
                pass
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and "private_key" in parsed:
                return parsed
        except Exception:
            pass
        return None

    @classmethod
    def _safe_init_app(cls, cred, name: str | None = None):
        try:
            if name:
                try:
                    return firebase_admin.get_app(name)
                except ValueError:
                    return firebase_admin.initialize_app(cred, name=name)
            else:
                try:
                    return firebase_admin.get_app()
                except ValueError:
                    return firebase_admin.initialize_app(cred)
        except Exception as e:
            logger.warning(f"Could not initialize Firebase app '{name or 'default'}': {e}")
            return None

    @classmethod
    def _initialize(cls):
        if cls._initialized:
            return

        try:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

            # 1. Kulloma Data App
            kulloma_dict = cls._parse_credential(
                os.getenv("FIREBASE_CREDENTIALS_KULLOMA_JSON") or os.getenv("FIREBASE_CREDENTIALS_KULLOMA_BASE64")
            )
            kulloma_path = None
            for p in [
                os.path.join(base_dir, "firebase-adminsdk-kullomadata.json"),
                os.path.join(os.getcwd(), "firebase-adminsdk-kullomadata.json"),
                "/app/firebase-adminsdk-kullomadata.json",
            ]:
                if os.path.exists(p):
                    kulloma_path = p
                    break

            if kulloma_dict:
                app_k = cls._safe_init_app(credentials.Certificate(kulloma_dict), name="kullomadata")
                if app_k:
                    cls._apps["kullomadata"] = app_k
            elif kulloma_path:
                app_k = cls._safe_init_app(credentials.Certificate(kulloma_path), name="kullomadata")
                if app_k:
                    cls._apps["kullomadata"] = app_k

            # 2. Mele Data App
            mele_dict = cls._parse_credential(
                os.getenv("FIREBASE_CREDENTIALS_MELEDATA_JSON") or os.getenv("FIREBASE_CREDENTIALS_MELEDATA_BASE64")
            )
            mele_path = None
            for p in [
                os.path.join(base_dir, "firebase-adminsdk-meledata.json"),
                os.path.join(os.getcwd(), "firebase-adminsdk-meledata.json"),
                "/app/firebase-adminsdk-meledata.json",
            ]:
                if os.path.exists(p):
                    mele_path = p
                    break

            if mele_dict:
                app_m = cls._safe_init_app(credentials.Certificate(mele_dict), name="meledata")
                if app_m:
                    cls._apps["meledata"] = app_m
            elif mele_path:
                app_m = cls._safe_init_app(credentials.Certificate(mele_path), name="meledata")
                if app_m:
                    cls._apps["meledata"] = app_m

            # 3. Default App
            default_dict = cls._parse_credential(
                os.getenv("FIREBASE_CREDENTIALS_JSON") or os.getenv("FIREBASE_CREDENTIALS_BASE64")
            )
            default_path = os.getenv("FIREBASE_CREDENTIALS_PATH", "firebase-adminsdk.json")
            found_default = None
            potential_paths = [
                default_path,
                os.path.join(base_dir, "firebase-adminsdk.json"),
                os.path.join(os.getcwd(), "firebase-adminsdk.json"),
                "/app/firebase-adminsdk.json",
                "/etc/secrets/firebase-adminsdk.json",
            ]
            for p in potential_paths:
                if p and os.path.exists(p):
                    found_default = p
                    break

            if default_dict:
                app_d = cls._safe_init_app(credentials.Certificate(default_dict))
                if app_d:
                    cls._apps["default"] = app_d
            elif found_default:
                app_d = cls._safe_init_app(credentials.Certificate(found_default))
                if app_d:
                    cls._apps["default"] = app_d

            # If default wasn't set but we have one of the named apps, alias it as default
            if "default" not in cls._apps:
                for k, a in cls._apps.items():
                    cls._apps["default"] = a
                    break

            cls._initialized = len(cls._apps) > 0
            logger.info(f"Firebase Admin initialized. Available apps: {list(cls._apps.keys())}")
        except Exception as e:
            logger.error(f"Failed to initialize Firebase Admin: {e}")

    @classmethod
    def get_app(cls, brand: str = None):
        cls._initialize()
        norm = (brand or "").strip().lower()
        if "kulloma" in norm:
            if "kullomadata" in cls._apps:
                return cls._apps["kullomadata"]
            default_app = cls._apps.get("default")
            if default_app and getattr(default_app, "project_id", "") == "kullomadata-c6c44":
                return default_app
            logger.warning("get_app: Kulloma Data requested but 'kullomadata' Firebase app is not loaded.")
            return None

        if "mele" in norm or "axis" in norm:
            if "meledata" in cls._apps:
                return cls._apps["meledata"]
            default_app = cls._apps.get("default")
            if default_app and getattr(default_app, "project_id", "") == "axisvtu":
                return default_app
            logger.warning("get_app: Mele Data requested but 'meledata' Firebase app is not loaded.")
            return None

        if "mz" in norm:
            if "mzdata" in cls._apps:
                return cls._apps["mzdata"]
            return None

        return cls._apps.get("default")

    @classmethod
    def send_push_notification(cls, token: str, title: str, body: str, data: dict = None, sound_type: str = "default", brand: str = None, image_url: str = None) -> bool:
        """Compatibility alias for webhooks and services calling send_push_notification."""
        return cls.send_to_token(token=token, title=title, body=body, data=data, sound_type=sound_type, brand=brand, image_url=image_url)

    @classmethod
    def send_to_token(cls, token: str, title: str, body: str, data: dict = None, sound_type: str = "default", brand: str = None, image_url: str = None) -> bool:
        cls._initialize()
        if not cls._initialized or not token:
            return False

        try:
            if data is None:
                data = {}
            data["sound_type"] = sound_type
            if image_url:
                data["image"] = image_url
                data["image_url"] = image_url

            if sound_type == "balance_success":
                android_config = messaging.AndroidConfig(
                    priority="high",
                    notification=messaging.AndroidNotification(
                        sound="balance_success",
                        channel_id="custom_sound_channel",
                        click_action="FLUTTER_NOTIFICATION_CLICK",
                        default_sound=False,
                        default_vibrate_timings=True,
                        image=image_url if image_url else None,
                    )
                )
                apns_config = messaging.APNSConfig(
                    payload=messaging.APNSPayload(
                        aps=messaging.Aps(
                            sound="balance_success.wav",
                            badge=1,
                            content_available=True,
                            mutable_content=True if image_url else False,
                        )
                    ),
                    fcm_options=messaging.APNSFCMOptions(image=image_url) if image_url else None
                )
            else:
                android_config = messaging.AndroidConfig(
                    priority="high",
                    notification=messaging.AndroidNotification(
                        sound="default",
                        channel_id="high_importance_channel",
                        click_action="FLUTTER_NOTIFICATION_CLICK",
                        default_sound=True,
                        default_vibrate_timings=True,
                        visibility="public",
                        priority="high",
                        image=image_url if image_url else None,
                    )
                )
                apns_config = messaging.APNSConfig(
                    payload=messaging.APNSPayload(
                        aps=messaging.Aps(
                            sound="default",
                            badge=1,
                            content_available=True,
                            mutable_content=True if image_url else False,
                        )
                    ),
                    fcm_options=messaging.APNSFCMOptions(image=image_url) if image_url else None
                )

            message = messaging.Message(
                notification=messaging.Notification(title=title, body=body, image=image_url if image_url else None),
                data=data,
                token=token,
                android=android_config,
                apns=apns_config,
            )
            app = cls.get_app(brand)
            if not app:
                logger.warning(f"No Firebase app available for brand '{brand}'. Push skipped.")
                return False

            try:
                response = messaging.send(message, app=app)
                logger.info(f"Successfully sent FCM message to {brand or 'default'} app: {response}")
                return True
            except Exception as send_err:
                logger.error(f"Error sending FCM message via '{brand or 'default'}' app: {send_err}")
                return False
        except Exception as e:
            logger.error(f"Error preparing FCM message: {e}")
            return False

    @classmethod
    def get_status(cls) -> dict:
        cls._initialize()
        apps_info = {}
        for name, app in cls._apps.items():
            apps_info[name] = getattr(app, "project_id", "unknown")
        default_app = cls._apps.get("default")
        return {
            "initialized": cls._initialized,
            "available_apps": apps_info,
            "default_project_id": getattr(default_app, "project_id", None) if default_app else None,
        }

    @classmethod
    def send_broadcast(
        cls,
        title: str,
        body: str,
        data: dict = None,
        image_url: str = None,
        tokens: list = None,
        target_brand: str = "all",
    ) -> bool:
        cls._initialize()
        if not cls._initialized:
            return False

        try:
            data_payload = dict(data or {})
            if image_url:
                data_payload["image"] = image_url
                data_payload["image_url"] = image_url

            android_config = messaging.AndroidConfig(
                priority="high",
                notification=messaging.AndroidNotification(
                    sound="default",
                    channel_id="high_importance_channel",
                    click_action="FLUTTER_NOTIFICATION_CLICK",
                    default_sound=True,
                    default_vibrate_timings=True,
                    visibility="public",
                    priority="high",
                    image=image_url if image_url else None,
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

            # 1. Strictly route to brand-isolated topic and dedicated Firebase app
            norm_brand = (target_brand or "all").strip().lower()
            if "kulloma" in norm_brand:
                topics = ["kullomadata_users"]
                app = cls.get_app("kullomadata")
            elif "mele" in norm_brand:
                topics = ["meledata_users"]
                app = cls.get_app("meledata")
            elif "mz" in norm_brand:
                topics = ["mzdata_users"]
                app = cls.get_app("mzdata")
            else:
                topics = ["all_users"]
                app = cls.get_app("default")

            if not app:
                logger.warning(f"No Firebase app configured for brand '{norm_brand}'. Broadcast skipped.")
                return False

            topic_sent = False
            for t in topics:
                try:
                    message = messaging.Message(
                        notification=messaging.Notification(title=title, body=body, image=image_url),
                        data=data_payload,
                        topic=t,
                        android=android_config,
                        apns=apns_config,
                    )
                    resp = messaging.send(message, app=app)
                    logger.info(f"FCM broadcast to topic '{t}' for '{norm_brand}' succeeded: {resp}")
                    topic_sent = True
                except Exception as top_err:
                    logger.warning(f"FCM broadcast to topic '{t}' failed: {top_err}")

            # 2. Only fallback to direct device token multicast if topic broadcast did not send (prevents duplicate delivery)
            if not topic_sent and tokens:
                unique_tokens = list({str(tok).strip() for tok in tokens if tok and str(tok).strip()})
                for i in range(0, len(unique_tokens), 500):
                    batch = unique_tokens[i:i+500]
                    try:
                        multicast_msg = messaging.MulticastMessage(
                            notification=messaging.Notification(title=title, body=body, image=image_url),
                            data=data_payload,
                            tokens=batch,
                            android=android_config,
                            apns=apns_config,
                        )
                        batch_res = messaging.send_each_for_multicast(multicast_msg, app=app)
                        logger.info(
                            f"FCM fallback multicast batch sent strictly for '{norm_brand}' ({getattr(app, 'project_id', '')}): "
                            f"{batch_res.success_count} success, {batch_res.failure_count} failures."
                        )
                    except Exception as multi_err:
                        logger.warning(f"FCM multicast batch failed for brand '{norm_brand}': {multi_err}")

            return True
        except Exception as e:
            logger.error(f"Error sending FCM broadcast: {e}")
            return False
