import httpx

url = "https://mzdata-1.onrender.com/api/v1/developer/data/purchase"
payload = {
    "network": "mtn",
    "phone_number": "09095263835",
    "plan_id": 423,
    "reference": "TEST_12345"
}

resp = httpx.post(url, json=payload, headers={"Authorization": "Bearer fake"})
print(resp.status_code)
print(resp.text)
