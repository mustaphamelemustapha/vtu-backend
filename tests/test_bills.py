from app.services.bills import (
    VTPassBillsProvider,
    _extract_purchased_pins,
    _extract_token,
    _normalize_exam_key,
)


def test_normalize_exam_key_aliases():
    assert _normalize_exam_key("waec-result-checker") == "waec"
    assert _normalize_exam_key("WAECDIRECT") == "waec"
    assert _normalize_exam_key("neco-token") == "neco"
    assert _normalize_exam_key("jamb-pin") == "jamb"


def test_extract_purchased_pins_from_cards_and_text():
    payload = {
        "cards": [{"Serial": "ABC", "Pin": "1234567890"}],
        "purchased_code": "Card 1 Pin: 99887766",
    }
    pins = _extract_purchased_pins(payload)
    assert pins == ["1234567890", "99887766"]


def test_extract_token_prefers_token_marker():
    assert _extract_token("Token: 445566778899") == "445566778899"
    assert _extract_token("PIN: 1234") == "1234"


def test_parse_result_pending_status_is_not_failed():
    provider = VTPassBillsProvider()
    parsed = provider._parse_result(
        {
            "code": "099",
            "response_description": "Transaction pending",
            "requestId": "REQ-1",
            "content": {"transactions": {"status": "pending"}},
        }
    )
    assert parsed.success is False
    assert parsed.external_reference == "REQ-1"
    assert "pending" in (parsed.message or "").lower()
    assert (parsed.meta or {}).get("vtpass", {}).get("status") == "pending"


def test_purchase_exam_pin_uses_vtpass_payload(monkeypatch):
    provider = VTPassBillsProvider()
    captured = {}

    def _fake_resolve(exam):
        assert exam == "waec"
        return "waec", "waecdirect"

    def _fake_post(path, payload):
        captured["path"] = path
        captured["payload"] = payload
        return {
            "code": "000",
            "requestId": "REQ-EXAM-1",
            "response_description": "TRANSACTION SUCCESSFUL",
            "purchased_code": "Pin: 1122334455",
            "content": {"transactions": {"status": "delivered", "transactionId": "TX-1"}},
        }

    monkeypatch.setattr(provider, "_resolve_exam_service_and_variation", _fake_resolve)
    monkeypatch.setattr(provider, "_post", _fake_post)

    result = provider.purchase_exam_pin("waec", 2, "08011112222")
    assert captured["path"] == "/pay"
    assert captured["payload"]["serviceID"] == "waec"
    assert captured["payload"]["variation_code"] == "waecdirect"
    assert captured["payload"]["quantity"] == 2
    assert captured["payload"]["phone"] == "08011112222"
    assert result.success is True
    assert (result.meta or {}).get("pins") == ["1122334455"]


def test_purchase_exam_pin_requires_phone():
    provider = VTPassBillsProvider()
    result = provider.purchase_exam_pin("waec", 1, None)
    assert result.success is False
    assert "phone number" in (result.message or "").lower()
