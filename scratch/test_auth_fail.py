import httpx

def test_auth_fail():
    user_id = "WRONG"
    api_key = "WRONG"
    disco_code = "11" # YEDC
    meter_no = "0281300022544"
    meter_type = "01" # Prepaid
    
    url = "https://www.nellobytesystems.com/APIVerifyElectricityV1.asp"
    params = {
        "UserID": user_id,
        "APIKey": api_key,
        "ElectricCompany": disco_code,
        "MeterNo": meter_no,
        "MeterType": meter_type
    }
    
    print(f"Testing with wrong credentials...")
    try:
        res = httpx.get(url, params=params, timeout=20)
        print(f"Status Code: {res.status_code}")
        print(f"Raw Response: {res.text}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_auth_fail()
