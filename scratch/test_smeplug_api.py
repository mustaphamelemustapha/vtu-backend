import os
import sys
sys.path.append("/Users/mustaphamelemustapha/Code/VTU/vtu-backend")
from app.providers.smeplug_provider import SMEPlugProvider

sme = SMEPlugProvider()
print("SMEPlug API test starting...")
print("Headers:", sme._get_headers())

import httpx
# Test with "phone"
payload = {
    "network_id": "1",
    "plan_id": "1", # Just dummy
    "phone": "08012345678"
}
url = f"{sme.base_url}/data/purchase"
try:
    with httpx.Client(timeout=sme.timeout) as client:
        r = client.post(url, json=payload, headers=sme._get_headers())
        print("Test 1 (phone):", r.status_code, r.text)
except Exception as e:
    print(e)
