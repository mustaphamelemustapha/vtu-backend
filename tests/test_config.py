from app.core.config import parse_cors_origins


def test_parse_cors_origins_csv():
    value = "http://localhost:5173, http://localhost:3000"
    assert parse_cors_origins(value) == [
        "http://localhost:5173",
        "http://localhost:3000",
    ]


def test_parse_cors_origins_json_list():
    value = '["http://localhost:5173", "https://vtu.example.com"]'
    assert parse_cors_origins(value) == [
        "http://localhost:5173",
        "https://vtu.example.com",
    ]


def test_parse_cors_origins_deduplicates():
    value = "http://localhost:5173,http://localhost:5173"
    assert parse_cors_origins(value) == ["http://localhost:5173"]
