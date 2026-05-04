import httpx
import sys

def test_verify():
    user_id = "CK101274841"
    api_key = "D7W924E74G4O1KQOY10MB553L320WDPM2I675LB11B03O5UO17IXNEH201LS5A7Y"
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
    
    print(f"Testing verification with params: {params}")
    try:
        res = httpx.get(url, params=params, timeout=20)
        print(f"Status Code: {res.status_code}")
        print(f"Raw Response: {res.text}")
        try:
            print(f"JSON Response: {res.json()}")
        except:
            print("Response is not JSON")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_verify()
