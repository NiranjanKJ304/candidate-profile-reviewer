"""Request ID middleware, security headers, CORS and the error envelope."""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI, Query

from app.core.middleware import SECURITY_HEADERS, resolve_request_id
from tests.conftest import AppFactory, client_for


def _add_test_routes(app: FastAPI) -> None:
    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("internal detail: postgresql://u:pw-leak@h/db")

    @app.get("/items")
    async def items(limit: int = Query(ge=1, le=10)) -> dict[str, int]:
        return {"limit": limit}


@pytest.fixture
def app(make_app: AppFactory) -> FastAPI:
    application = make_app()
    _add_test_routes(application)
    return application


class TestResolveRequestId:
    def test_generates_when_missing(self) -> None:
        rid = resolve_request_id(None)
        assert len(rid) == 32
        assert rid != resolve_request_id(None)

    @pytest.mark.parametrize("value", ["abc-123", "A.b_c-9", "x" * 128])
    def test_accepts_well_formed(self, value: str) -> None:
        assert resolve_request_id(value) == value

    @pytest.mark.parametrize(
        "value", ["", "x" * 129, "has space", "new\nline", "<script>", "a;b", "é"]
    )
    def test_replaces_malformed(self, value: str) -> None:
        rid = resolve_request_id(value)
        assert rid != value
        assert len(rid) == 32


class TestRequestIdMiddleware:
    async def test_response_has_generated_request_id(self, app: FastAPI) -> None:
        async with client_for(app) as client:
            resp = await client.get("/api/v1/health")
        assert len(resp.headers["x-request-id"]) == 32

    async def test_client_request_id_is_propagated(self, app: FastAPI) -> None:
        async with client_for(app) as client:
            resp = await client.get("/api/v1/health", headers={"X-Request-ID": "trace-42"})
        assert resp.headers["x-request-id"] == "trace-42"

    async def test_injection_attempt_is_replaced(self, app: FastAPI) -> None:
        async with client_for(app) as client:
            resp = await client.get("/api/v1/health", headers={"X-Request-ID": "bad id\tvalue"})
        assert resp.headers["x-request-id"] != "bad id\tvalue"

    async def test_access_log_contains_request_details(
        self, app: FastAPI, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger="app.access")
        async with client_for(app) as client:
            await client.get("/api/v1/health", headers={"X-Request-ID": "log-me"})
        records = [r for r in caplog.records if r.name == "app.access"]
        assert records
        rec = records[-1]
        assert rec.__dict__["http_path"] == "/api/v1/health"
        assert rec.__dict__["http_status"] == 200
        assert rec.__dict__["duration_ms"] >= 0


class TestErrorEnvelope:
    async def test_404_uses_envelope_with_request_id(self, app: FastAPI) -> None:
        async with client_for(app) as client:
            resp = await client.get("/does-not-exist", headers={"X-Request-ID": "rid-404"})
        assert resp.status_code == 404
        assert resp.json() == {
            "error": {"code": "not_found", "message": "Not Found", "request_id": "rid-404"}
        }

    async def test_422_hides_submitted_values(self, app: FastAPI) -> None:
        async with client_for(app) as client:
            resp = await client.get("/items", params={"limit": "secret-pii-value"})
        assert resp.status_code == 422
        body = resp.json()["error"]
        assert body["code"] == "validation_error"
        assert body["details"][0]["loc"] == ["query", "limit"]
        assert "secret-pii-value" not in resp.text

    async def test_422_missing_parameter(self, app: FastAPI) -> None:
        async with client_for(app) as client:
            resp = await client.get("/items")
        assert resp.status_code == 422
        assert resp.json()["error"]["details"][0]["type"] == "missing"

    async def test_500_is_generic_and_keeps_headers(
        self, app: FastAPI, caplog: pytest.LogCaptureFixture
    ) -> None:
        async with client_for(app, raise_app_exceptions=False) as client:
            resp = await client.get(
                "/boom",
                headers={"X-Request-ID": "rid-500", "Origin": "http://localhost:3000"},
            )
        assert resp.status_code == 500
        assert resp.json() == {
            "error": {
                "code": "internal_error",
                "message": "An unexpected error occurred",
                "request_id": "rid-500",
            }
        }
        assert "pw-leak" not in resp.text
        # Outer middleware still applied to the 500 response:
        assert resp.headers["x-request-id"] == "rid-500"
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"
        assert any("unhandled exception" in r.getMessage() for r in caplog.records)


class TestSecurityAndCors:
    async def test_security_headers_present(self, app: FastAPI) -> None:
        async with client_for(app) as client:
            resp = await client.get("/api/v1/health")
        for header, value in SECURITY_HEADERS.items():
            assert resp.headers[header] == value

    async def test_cors_allows_configured_origin(self, app: FastAPI) -> None:
        async with client_for(app) as client:
            resp = await client.options(
                "/api/v1/health",
                headers={
                    "Origin": "http://localhost:3000",
                    "Access-Control-Request-Method": "GET",
                },
            )
        assert resp.status_code == 200
        assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"

    async def test_cors_rejects_unknown_origin(self, app: FastAPI) -> None:
        async with client_for(app) as client:
            resp = await client.get("/api/v1/health", headers={"Origin": "https://evil.test"})
        assert "access-control-allow-origin" not in resp.headers

    async def test_cors_exposes_request_id(self, app: FastAPI) -> None:
        async with client_for(app) as client:
            resp = await client.get("/api/v1/health", headers={"Origin": "http://localhost:3000"})
        assert "x-request-id" in resp.headers["access-control-expose-headers"].lower()

    async def test_docs_enabled_outside_production(self, app: FastAPI) -> None:
        async with client_for(app) as client:
            assert (await client.get("/openapi.json")).status_code == 200

    async def test_docs_disabled_in_production(self, make_app: AppFactory) -> None:
        app = make_app(environment="production", cors_origins="https://app.example.com")
        async with client_for(app) as client:
            assert (await client.get("/docs")).status_code == 404
            assert (await client.get("/openapi.json")).status_code == 404
