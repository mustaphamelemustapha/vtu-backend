import sys
import os
import httpx

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import get_settings

def test_brevo(recipient_email: str):
    settings = get_settings()
    api_key = settings.brevo_api_key or os.getenv("BREVO_API_KEY")
    email_from = settings.email_from or os.getenv("EMAIL_FROM", "KULLOMA DATA <support@kullomadata.com>")

    if not api_key:
        print("[ERROR] BREVO_API_KEY is not set.")
        return

    from app.services.email import _parse_from
    name, from_email = _parse_from(email_from)

    print(f"Testing Brevo with:")
    print(f"  From Name:  {name}")
    print(f"  From Email: {from_email}")
    print(f"  Recipient:  {recipient_email}")
    print(f"  API Key:    {api_key[:12]}...{api_key[-6:]}")

    payload = {
        "sender": {"name": name or "KULLOMA DATA", "email": from_email},
        "to": [{"email": recipient_email}],
        "subject": "KULLOMA DATA - Test Verification Code",
        "htmlContent": "<p>This is a test email from KULLOMA DATA. Your test verification code is: <strong>123456</strong>.</p>",
    }
    headers = {"api-key": api_key, "Content-Type": "application/json", "Accept": "application/json"}
    
    try:
        with httpx.Client(timeout=15) as client:
            res = client.post("https://api.brevo.com/v3/smtp/email", json=payload, headers=headers)
            print("Response Status Code:", res.status_code)
            print("Response Body:", res.text)
            if res.status_code < 400:
                print("SUCCESS: Test email successfully sent via Brevo!")
            else:
                print(f"FAILED: Brevo returned status {res.status_code}")
    except Exception as e:
        print(f"[EXCEPTION] Failed to send: {e}")

if __name__ == "__main__":
    to = sys.argv[1] if len(sys.argv) > 1 else "kullomadata@gmail.com"
    test_brevo(to)
