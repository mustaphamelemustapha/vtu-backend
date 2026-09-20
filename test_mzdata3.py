import httpx

url = "https://mzdata-1.onrender.com/api/v1/developer/data/purchase"

# Send completely invalid payload with no authorization
resp = httpx.post(url, json={})
print(resp.status_code)
print(resp.text[:500])
