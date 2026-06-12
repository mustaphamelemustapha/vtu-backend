from app.services.amigo import (
    normalize_plan_code,
    resolve_network_id,
    split_plan_code,
    canonical_plan_code,
)


def test_normalize_plan_code_numeric():
    assert normalize_plan_code("5000") == 5000


def test_normalize_plan_code_string():
    assert normalize_plan_code("MTN_1GB_30D") == "MTN_1GB_30D"


def test_resolve_network_id_from_network_name():
    assert resolve_network_id("mtn") == 1
    assert resolve_network_id("glo") == 2
    assert resolve_network_id("airtel") == 4
    assert resolve_network_id("9mobile") == 9


def test_split_plan_code():
    assert split_plan_code("amigo:mtn:1001") == ("amigo", "1001")
    assert split_plan_code("1001") == (None, "1001")


def test_canonical_plan_code():
    assert canonical_plan_code("amigo", "mtn", "1001") == "amigo:mtn:1001"
    assert canonical_plan_code("amigo", "mtn", "amigo:mtn:1001") == "amigo:mtn:1001"

