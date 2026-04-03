from app.services.bills import (
    ClubKonnectBillsProvider,
    MockBillsProvider,
    VTPassBillsProvider,
    _extract_purchased_pins,
    _extract_token,
    get_bills_provider,
    _normalize_exam_key,
    settings as bills_settings,
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


def test_clubkonnect_purchase_airtime_maps_payload(monkeypatch):
    provider = ClubKonnectBillsProvider()
    captured = {}

    def _fake_request(endpoint, params):
        captured["endpoint"] = endpoint
        captured["params"] = params
        return {"status": "200", "OrderID": "CK-1"}

    monkeypatch.setattr(provider, "_request", _fake_request)
    result = provider.purchase_airtime("mtn", "08141114647", 200.0)
    assert captured["endpoint"] == "APIAirtimeV1.asp"
    assert captured["params"]["MobileNetwork"] == "01"
    assert captured["params"]["MobileNumber"] == "08141114647"
    assert captured["params"]["Amount"] == 200
    assert result.success is True
    assert result.external_reference == "CK-1"
    assert (result.meta or {}).get("clubkonnect", {}).get("status") == "success"


def test_clubkonnect_pending_maps_to_pending_message():
    provider = ClubKonnectBillsProvider()
    result = provider._parse_result({"statuscode": "100", "status": "ORDER_RECEIVED", "OrderID": "CK-2"}, action="airtime")
    assert result.success is False
    assert "pending" in (result.message or "").lower()
    assert (result.meta or {}).get("clubkonnect", {}).get("status") == "pending"


def test_clubkonnect_statuscode_200_is_success():
    provider = ClubKonnectBillsProvider()
    result = provider._parse_result({"statuscode": "200", "status": "ORDER_COMPLETED", "orderid": "CK-3"}, action="airtime")
    assert result.success is True
    assert result.external_reference == "CK-3"


def test_clubkonnect_invalid_credentials_fails():
    provider = ClubKonnectBillsProvider()
    result = provider._parse_result({"status": "INVALID_CREDENTIALS"}, action="airtime")
    assert result.success is False
    assert (result.meta or {}).get("clubkonnect", {}).get("status") == "failed"


def test_clubkonnect_fetch_data_variations_prefers_mobile_network_field(monkeypatch):
    provider = ClubKonnectBillsProvider()

    def _fake_request(endpoint, params):
        assert endpoint == "APIDatabundlePlansV2.asp"
        return {
            "MOBILE_NETWORK": [
                {"ID": "499.91", "MobileNetwork": "04", "DataPlan": "499.91", "Amount": "499.91"},
                {"ID": "1000.0", "MobileNetwork": "01", "DataPlan": "1000.0", "Amount": "567"},
            ]
        }

    monkeypatch.setattr(provider, "_request", _fake_request)
    rows = provider.fetch_data_variations("airtel")
    assert len(rows) == 1
    assert str(rows[0].get("DataPlan")) == "499.91"


def test_clubkonnect_fetch_data_variations_handles_grouped_payload(monkeypatch):
    provider = ClubKonnectBillsProvider()

    def _fake_request(endpoint, params):
        assert endpoint == "APIDatabundlePlansV2.asp"
        return {
            "MOBILE_NETWORK": [
                {
                    "ID": "04",
                    "Network": "Airtel",
                    "DataPlans": [
                        {"DataPlan": "499.91", "Amount": "499.91", "DataType": "1GB - 1 day"},
                        {"DataPlan": "599.91", "Amount": "599.91", "DataType": "1.5GB - 2 days"},
                    ],
                }
            ]
        }

    monkeypatch.setattr(provider, "_request", _fake_request)
    rows = provider.fetch_data_variations("airtel")
    assert len(rows) == 2
    assert all(str(row.get("MobileNetwork")) == "04" for row in rows)


def test_clubkonnect_fetch_data_variations_handles_v2_product_shape(monkeypatch):
    provider = ClubKonnectBillsProvider()

    def _fake_request(endpoint, params):
        assert endpoint == "APIDatabundlePlansV2.asp"
        return {
            "MOBILE_NETWORK": {
                "Airtel": [
                    {
                        "ID": "04",
                        "PRODUCT": [
                            {
                                "PRODUCT_ID": "499.91",
                                "PRODUCT_NAME": "1GB - 1 day (Awoof Data)",
                                "PRODUCT_AMOUNT": "499.91",
                            }
                        ],
                    }
                ],
                "MTN": [
                    {
                        "ID": "01",
                        "PRODUCT": [
                            {
                                "PRODUCT_ID": "1000.0",
                                "PRODUCT_NAME": "1 GB - 7 days (SME)",
                                "PRODUCT_AMOUNT": "567",
                            }
                        ],
                    }
                ],
            }
        }

    monkeypatch.setattr(provider, "_request", _fake_request)
    rows = provider.fetch_data_variations("airtel")
    assert len(rows) == 1
    assert str(rows[0].get("DataPlan")) == "499.91"
    assert str(rows[0].get("Amount")) == "499.91"


def test_get_bills_provider_prefers_clubkonnect_in_auto(monkeypatch):
    monkeypatch.setattr(bills_settings, "bills_provider", "auto", raising=False)
    monkeypatch.setattr(bills_settings, "clubkonnect_enabled", True, raising=False)
    monkeypatch.setattr(bills_settings, "clubkonnect_user_id", "uid", raising=False)
    monkeypatch.setattr(bills_settings, "clubkonnect_api_key", "key", raising=False)
    monkeypatch.setattr(bills_settings, "vtpass_enabled", True, raising=False)
    monkeypatch.setattr(bills_settings, "vtpass_api_key", "vk", raising=False)
    monkeypatch.setattr(bills_settings, "vtpass_secret_key", "vs", raising=False)
    provider = get_bills_provider()
    assert isinstance(provider, ClubKonnectBillsProvider)


def test_get_bills_provider_falls_back_to_mock_when_forced_without_creds(monkeypatch):
    monkeypatch.setattr(bills_settings, "bills_provider", "clubkonnect", raising=False)
    monkeypatch.setattr(bills_settings, "clubkonnect_user_id", "", raising=False)
    monkeypatch.setattr(bills_settings, "clubkonnect_api_key", "", raising=False)
    provider = get_bills_provider()
    assert isinstance(provider, MockBillsProvider)
