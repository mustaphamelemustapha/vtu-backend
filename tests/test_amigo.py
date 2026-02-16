from app.services.amigo import (
    normalize_amigo_base_url,
    normalize_plan_code,
    parse_efficiency_plans,
    resolve_network_id,
)


def test_normalize_plan_code_numeric():
    assert normalize_plan_code("5000") == 5000


def test_normalize_plan_code_string():
    assert normalize_plan_code("MTN_1GB_30D") == "MTN_1GB_30D"


def test_resolve_network_id_from_plan_code():
    assert resolve_network_id("mtn", "5000") == 1


def test_resolve_network_id_from_network_name():
    assert resolve_network_id("glo", None) == 2


def test_resolve_network_id_unknown_network():
    assert resolve_network_id("unknown", "nope") is None


def test_normalize_amigo_base_url_legacy_placeholder():
    assert normalize_amigo_base_url("https://api.amigo.com") == "https://amigo.ng/api"


def test_normalize_amigo_base_url_adds_api_path_for_root_domain():
    assert normalize_amigo_base_url("https://amigo.ng") == "https://amigo.ng/api"


def test_parse_efficiency_plans_extracts_rows():
    payload = {
        "ok": True,
        "MTN": [
            {"plan_id": 5000, "data_capacity": 1, "validity": 30, "price": 430},
        ],
    }
    parsed = parse_efficiency_plans(payload)
    assert len(parsed) == 1
    assert parsed[0]["network"] == "mtn"
    assert parsed[0]["plan_code"] == "5000"
    assert parsed[0]["data_size"] == "1GB"
    assert parsed[0]["validity"] == "30d"


def test_purchase_data_falls_back_on_404(monkeypatch):
    # Ensure the client tries alternate paths when configured purchase path is wrong.
    from app.services.amigo import AmigoApiError, AmigoClient, settings as amigo_settings

    monkeypatch.setattr(amigo_settings, "amigo_test_mode", False, raising=False)
    monkeypatch.setattr(amigo_settings, "amigo_data_purchase_path", "/wrong/", raising=False)

    client = AmigoClient()
    calls = []

    def _fake_request(method, path, payload=None, **kwargs):
        calls.append(path)
        if path == "/wrong/":
            raise AmigoApiError("Not found", status_code=404)
        if path == "/data/":
            return {"success": True, "status": "delivered", "reference": "OK"}
        raise AmigoApiError("Unexpected", status_code=400)

    monkeypatch.setattr(client, "_request", _fake_request)
    out = client.purchase_data({"network": 1, "mobile_number": "080", "plan": 1001})
    assert out["success"] is True
    assert "/wrong/" in calls
    assert "/data/" in calls
