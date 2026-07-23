import sys
import os
sys.path.insert(0, os.path.abspath("."))
from app.services.bills import ClubKonnectBillsProvider

ck = ClubKonnectBillsProvider()
res = ck._parse_result({"status": "ACCOUNT_MISMATCH_CONTACT_SUPPORT"}, action="airtime")
print(res.success, res.message, res.meta)
