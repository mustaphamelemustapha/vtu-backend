import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.providers.smeplug_provider import SMEPlugProvider

def main():
    sme = SMEPlugProvider()
    res = sme.purchase_data(network_id=2, plan_id="296", phone="09124989418", reference="TEST_AIRTEL_1234")
    print(res)

if __name__ == "__main__":
    main()
