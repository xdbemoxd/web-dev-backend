import redis
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core import cache
from app.core.db import get_engine


def test_liveness(client: TestClient) -> None:
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
    assert res.headers["X-Request-ID"]
    assert res.headers["X-Content-Type-Options"] == "nosniff"


def test_readiness_with_all_dependencies(client: TestClient) -> None:
    res = client.get("/api/v1/health/ready")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "db": "ok", "redis": "ok"}


def test_readiness_degrades_without_redis(client: TestClient, monkeypatch) -> None:
    dead = redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2)
    monkeypatch.setattr(cache, "get_client", lambda: dead)

    res = client.get("/api/v1/health/ready")

    assert res.status_code == 200
    assert res.json() == {"status": "degraded", "db": "ok", "redis": "down"}


def test_cache_helpers_do_not_raise_without_redis(monkeypatch) -> None:
    dead = redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2)
    monkeypatch.setattr(cache, "get_client", lambda: dead)

    cache.set_json("k", {"a": 1})
    assert cache.get_json("k") is None

    calls = []

    @cache.cached("test:fn")
    def compute(x: int) -> int:
        calls.append(x)
        return x * 2

    assert compute(2) == 4  # sin Redis la función se ejecuta igual
    assert calls == [2]


def test_cached_decorator_hits_redis() -> None:
    cache.delete("test:double:21")
    calls = []

    @cache.cached("test:double", ttl=30)
    def double(x: int) -> int:
        calls.append(x)
        return x * 2

    assert double(21) == 42
    assert double(21) == 42
    assert calls == [21]


def test_extensions_installed() -> None:
    with get_engine().connect() as conn:
        installed = set(conn.execute(text("SELECT extname FROM pg_extension")).scalars())
    assert {"citext", "unaccent", "pg_trgm"} <= installed
