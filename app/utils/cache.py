import time

_cache = {}


def get_cached(key: str):
    entry = _cache.get(key)
    if not entry:
        return None
    value, expires_at = entry
    if expires_at < time.time():
        _cache.pop(key, None)
        return None
    return value


def set_cached(key: str, value, ttl_seconds: int = 60):
    _cache[key] = (value, time.time() + ttl_seconds)
