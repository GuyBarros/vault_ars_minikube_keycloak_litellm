"""Integration-style tests for the FastAPI routes."""
import time
from unittest.mock import MagicMock, patch

import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from api.main import app
from broker.broker import IdentityTokenResult
from exceptions.errors import (
    CacheError,
    VaultAuthenticationError,
    VaultTokenGenerationError,
)


def _make_jwt(exp_offset: int = 3600) -> str:
    payload = {"sub": "svc", "exp": int(time.time()) + exp_offset}
    return jwt.encode(payload, "secret", algorithm="HS256")


@pytest.fixture()
def async_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestExchangeToken:
    async def test_request_id_header_is_preserved(self, async_client):
        async with async_client as client:
            resp = await client.get("/healthz", headers={"X-Request-ID": "req-123"})

        assert resp.status_code == 200
        assert resp.headers["X-Request-ID"] == "req-123"

    async def test_request_id_header_is_not_added_when_missing(self, async_client):
        async with async_client as client:
            resp = await client.get("/healthz")

        assert resp.status_code == 200
        assert "X-Request-ID" not in resp.headers

    async def test_success_cache_miss(self, async_client):
        token = _make_jwt()
        result = IdentityTokenResult(identity_token=token, expires_at=int(time.time()) + 3600, cached=False)

        with patch("api.routes._broker.get_signed_identity_token", return_value=result):
            async with async_client as client:
                resp = await client.post(
                    "/v1/identity/token",
                    json={"vault_token": "hvs.test", "role_name": "payments-api"},
                )

        assert resp.status_code == 200
        body = resp.json()
        assert body["identity_token"] == token
        assert body["cached"] is False

    async def test_success_cache_hit(self, async_client):
        token = _make_jwt()
        result = IdentityTokenResult(identity_token=token, expires_at=int(time.time()) + 3600, cached=True)

        with patch("api.routes._broker.get_signed_identity_token", return_value=result):
            async with async_client as client:
                resp = await client.post(
                    "/v1/identity/token",
                    json={"vault_token": "hvs.test", "role_name": "payments-api"},
                )

        assert resp.status_code == 200
        assert resp.json()["cached"] is True

    async def test_auth_failure_returns_401(self, async_client):
        with patch(
            "api.routes._broker.get_signed_identity_token",
            side_effect=VaultAuthenticationError("forbidden"),
        ):
            async with async_client as client:
                resp = await client.post(
                    "/v1/identity/token",
                    json={"vault_token": "bad-tok", "role_name": "payments-api"},
                )

        assert resp.status_code == 401

    async def test_generation_error_returns_500(self, async_client):
        with patch(
            "api.routes._broker.get_signed_identity_token",
            side_effect=VaultTokenGenerationError("vault exploded"),
        ):
            async with async_client as client:
                resp = await client.post(
                    "/v1/identity/token",
                    json={"vault_token": "hvs.test", "role_name": "payments-api"},
                )

        assert resp.status_code == 500

    async def test_cache_error_returns_500(self, async_client):
        with patch(
            "api.routes._broker.get_signed_identity_token",
            side_effect=CacheError("cache broke"),
        ):
            async with async_client as client:
                resp = await client.post(
                    "/v1/identity/token",
                    json={"vault_token": "hvs.test", "role_name": "payments-api"},
                )

        assert resp.status_code == 500

    async def test_vault_unavailable_returns_503(self, async_client):
        import requests

        with patch(
            "api.routes._broker.get_signed_identity_token",
            side_effect=requests.exceptions.ConnectionError("connection refused"),
        ):
            async with async_client as client:
                resp = await client.post(
                    "/v1/identity/token",
                    json={"vault_token": "hvs.test", "role_name": "payments-api"},
                )

        assert resp.status_code == 503

    async def test_missing_fields_returns_422(self, async_client):
        async with async_client as client:
            resp = await client.post("/v1/identity/token", json={"vault_token": "hvs.test"})
        assert resp.status_code == 422

    async def test_healthz(self, async_client):
        async with async_client as client:
            resp = await client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
