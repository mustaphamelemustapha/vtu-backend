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
