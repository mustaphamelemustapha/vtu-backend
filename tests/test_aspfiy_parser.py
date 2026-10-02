import pytest
from app.services.aspfiy import AspfiyService

def test_parse_reserved_account_response():
    # Direct dictionary snake_case
    r1 = {"account_number": "1717002660", "account_name": "Yahaya Musa", "bank_name": "Paga"}
    assert AspfiyService.parse_reserved_account_response(r1) == {
        "account_number": "1717002660",
        "account_name": "Yahaya Musa",
        "bank_name": "Paga"
    }

    # Direct dictionary camelCase
    r2 = {"accountNumber": "1717002660", "accountName": "Yahaya Musa", "bankName": "Paga"}
    assert AspfiyService.parse_reserved_account_response(r2) == {
        "account_number": "1717002660",
        "account_name": "Yahaya Musa",
        "bank_name": "Paga"
    }

    # Wrapped in data dict
    r3 = {"status": True, "data": {"account_number": "1717002660", "account_name": "Yahaya Musa", "bank_name": "Paga"}}
    assert AspfiyService.parse_reserved_account_response(r3) == {
        "account_number": "1717002660",
        "account_name": "Yahaya Musa",
        "bank_name": "Paga"
    }

    # Wrapped in data list (typical Paga response)
    r4 = {"status": True, "data": [{"account_number": "1717002660", "account_name": "Yahaya Musa", "bank_name": "Paga"}]}
    assert AspfiyService.parse_reserved_account_response(r4) == {
        "account_number": "1717002660",
        "account_name": "Yahaya Musa",
        "bank_name": "Paga"
    }

    # Wrapped in data.account dict
    r5 = {"status": True, "data": {"account": {"accountNumber": "1717002660", "accountName": "Yahaya Musa", "bankName": "Paga"}}}
    assert AspfiyService.parse_reserved_account_response(r5) == {
        "account_number": "1717002660",
        "account_name": "Yahaya Musa",
        "bank_name": "Paga"
    }

    # Wrapped in data.accounts list
    r6 = {"status": True, "data": {"accounts": [{"account_number": "1717002660", "bank_name": "Paga"}]}}
    assert AspfiyService.parse_reserved_account_response(r6) == {
        "account_number": "1717002660",
        "account_name": "Mele Data Customer",
        "bank_name": "Paga"
    }

    # Invalid / empty
    assert AspfiyService.parse_reserved_account_response({"status": False, "message": "Failed"}) is None
    assert AspfiyService.parse_reserved_account_response({}) is None
    assert AspfiyService.parse_reserved_account_response(None) is None
