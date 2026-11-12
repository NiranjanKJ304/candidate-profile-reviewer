"""/health and /ready endpoints through the full middleware stack (fake dependencies)."""

from __future__ import annotations

from redis.exceptions import ConnectionError as RedisConnectionError

from app import __version__
from tests.conftest import AppFactory, FakeEngine, FakeRedis, client_for


class TestHealth:
    async def test_health_ok(self, make_app: AppFactory) -> None:
        async with client_for(make_app()) as client:
            resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["version"] == __version__
        assert body["environment"] == "test"
        assert body["service"] == "Candidate Intelligence API"
        assert body["timestamp"].endswith(("Z", "+00:00"))

    async def test_health_does_not_touch_dependencies(self, make_app: AppFactory) -> None:
        engine = FakeEngine(connect_error=RuntimeError("db down"))
        async with client_for(make_app(engine=engine)) as client:
            resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        assert engine.connect_calls == 0

    async def test_head_is_not_allowed(self, make_app: AppFactory) -> None:
        async with client_for(make_app()) as client:
            resp = await client.post("/api/v1/health")
        assert resp.status_code == 405
        assert resp.json()["error"]["code"] == "method_not_allowed"

    async def test_custom_prefix(self, make_app: AppFactory) -> None:
        async with client_for(make_app(api_v1_prefix="/svc/v1")) as client:
            assert (await client.get("/svc/v1/health")).status_code == 200
            assert (await client.get("/api/v1/health")).status_code == 404


class TestReadiness:
    async def test_ready_when_all_dependencies_ok(self, make_app: AppFactory) -> None:
        async with client_for(make_app()) as client:
            resp = await client.get("/api/v1/ready")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ready"
        assert set(body["checks"]) == {"postgres", "redis"}
        assert body["checks"]["postgres"]["status"] == "ok"
        assert body["checks"]["redis"]["status"] == "ok"
        assert resp.headers["cache-control"] == "no-store"

    async def test_postgres_down_redis_up(self, make_app: AppFactory) -> None:
        app = make_app(engine=FakeEngine(connect_error=ConnectionRefusedError()))
        async with client_for(app) as client:
            resp = await client.get("/api/v1/ready")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "not_ready"
        assert body["checks"]["postgres"] == {
            "status": "error",
            "latency_ms": None,
            "error": "ConnectionRefusedError",
        }
        assert body["checks"]["redis"]["status"] == "ok"

    async def test_redis_down_postgres_up(self, make_app: AppFactory) -> None:
        app = make_app(redis=FakeRedis(error=RedisConnectionError("down")))
        async with client_for(app) as client:
            resp = await client.get("/api/v1/ready")
        assert resp.status_code == 503
        assert resp.json()["checks"]["postgres"]["status"] == "ok"
        assert resp.json()["checks"]["redis"]["status"] == "error"

    async def test_both_down(self, make_app: AppFactory) -> None:
        app = make_app(
            engine=FakeEngine(connect_error=OSError()),
            redis=FakeRedis(error=RedisConnectionError()),
        )
        async with client_for(app) as client:
            resp = await client.get("/api/v1/ready")
        assert resp.status_code == 503
        checks = resp.json()["checks"]
        assert checks["postgres"]["status"] == checks["redis"]["status"] == "error"

    async def test_timeouts_are_reported_and_bounded(self, make_app: AppFactory) -> None:
        import time

        app = make_app(
            engine=FakeEngine(delay=5),
            redis=FakeRedis(delay=5),
            readiness_timeout_seconds=0.2,
        )
        start = time.perf_counter()
        async with client_for(app) as client:
            resp = await client.get("/api/v1/ready")
        elapsed = time.perf_counter() - start
        assert resp.status_code == 503
        checks = resp.json()["checks"]
        assert checks["postgres"]["status"] == "timeout"
        assert checks["redis"]["status"] == "timeout"
        # Probes run concurrently: total ~= one timeout, not the sum of both.
        assert elapsed < 1.0

    async def test_error_details_never_include_credentials(self, make_app: AppFactory) -> None:
        exc = OSError("connect failed postgresql+asyncpg://tester:s3cr3t-pw@db.internal")
        async with client_for(make_app(engine=FakeEngine(connect_error=exc))) as client:
            resp = await client.get("/api/v1/ready")
        assert "s3cr3t-pw" not in resp.text
        assert "db.internal" not in resp.text
