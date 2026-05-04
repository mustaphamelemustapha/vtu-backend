import httpx

def test_discos():
    user_id = "CK101274841"
    api_key = "D7W924E74G4O1KQOY10MB553L320WDPM2I675LB11B03O5UO17IXNEH201LS5A7Y"
    
    url = "https://www.nellobytesystems.com/APIElectricityDiscosV2.asp"
    params = {
        "UserID": user_id,
        "APIKey": api_key,
    }
    
    print(f"Fetching discos...")
    try:
        res = httpx.get(url, params=params, timeout=20)
        print(f"Status Code: {res.status_code}")
        print(f"Raw Response: {res.text}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_discos()
