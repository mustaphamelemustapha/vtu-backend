from app.services.amigo import normalize_plan_code, resolve_network_id


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
