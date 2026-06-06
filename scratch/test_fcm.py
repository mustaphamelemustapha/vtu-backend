import os
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.push_notification import PushNotificationService

def test_firebase_init():
    print("Initializing Firebase Admin Service...")
    PushNotificationService._initialize()
    print(f"Initialization status: {PushNotificationService._initialized}")
    
    if PushNotificationService._initialized:
        print("Success! Firebase Admin SDK credentials are correct and initialized successfully.")
    else:
        print("Warning: Firebase initialization failed. Check your credential file.")

if __name__ == "__main__":
    test_firebase_init()
