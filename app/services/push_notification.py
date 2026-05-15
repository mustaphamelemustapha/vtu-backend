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
        
        # In a real setup, place your firebase-adminsdk.json file in the root
        # of the backend directory (or configure FIREBASE_CREDENTIALS_PATH).
        cred_path = os.getenv("FIREBASE_CREDENTIALS_PATH", "firebase-adminsdk.json")
        if not os.path.exists(cred_path):
            logger.warning(f"Firebase config not found at {cred_path}. Push disabled.")
            return

        try:
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
            cls._initialized = True
            logger.info("Firebase Admin initialized successfully.")
        except ValueError:
            # Already initialized
            cls._initialized = True
        except Exception as e:
            logger.error(f"Failed to initialize Firebase Admin: {e}")

    @classmethod
    def send_to_token(cls, token: str, title: str, body: str, data: dict = None) -> bool:
        cls._initialize()
        if not cls._initialized or not token:
            return False

        try:
            message = messaging.Message(
                notification=messaging.Notification(title=title, body=body),
                data=data or {},
                token=token,
            )
            response = messaging.send(message)
            logger.info(f"Successfully sent FCM message: {response}")
            return True
        except Exception as e:
            logger.error(f"Error sending FCM message: {e}")
            return False

    @classmethod
    def send_broadcast(cls, title: str, body: str, data: dict = None) -> bool:
        # For sending to all users, the flutter app should subscribe to an 'all' topic
        cls._initialize()
        if not cls._initialized:
            return False

        try:
            message = messaging.Message(
                notification=messaging.Notification(title=title, body=body),
                data=data or {},
                topic="all_users",
            )
            response = messaging.send(message)
            logger.info(f"Successfully sent FCM broadcast: {response}")
            return True
        except Exception as e:
            logger.error(f"Error sending FCM broadcast: {e}")
            return False
