from app.api.v1.endpoints.transactions import _extract_recipient_phone


def test_extract_recipient_phone_plain_digits():
    assert _extract_recipient_phone("Data purchase to 09012345678") == "09012345678"


def test_extract_recipient_phone_with_plus_and_spaces():
    assert _extract_recipient_phone("Data purchase for +234 901 234 5678") == "+2349012345678"


def test_extract_recipient_phone_returns_none_for_missing_number():
    assert _extract_recipient_phone("Data purchase completed") is None
