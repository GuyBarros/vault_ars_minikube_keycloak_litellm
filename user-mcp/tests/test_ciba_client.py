from __future__ import annotations

import ciba_client as ciba_client_module
from ciba_client import CibaClient


class _FakeResponse:
    def __init__(self, status_code: int, json_body: dict):
        self.status_code = status_code
        self._json_body = json_body

    def json(self):
        return self._json_body


class _FakeAsyncClient:
    def __init__(self, calls: list, responses: list, *args, **kwargs):
        self._calls = calls
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, url, data=None):
        self._calls.append((url, dict(data or {})))
        return self._responses.pop(0)


def _patch_client(monkeypatch, calls, responses):
    monkeypatch.setattr(
        ciba_client_module.httpx,
        "AsyncClient",
        lambda *a, **k: _FakeAsyncClient(calls, responses, *a, **k),
    )

    async def fake_sleep(seconds):
        return None

    monkeypatch.setattr(ciba_client_module.asyncio, "sleep", fake_sleep)


async def test_delegation_actor_sent_when_actor_token_present(monkeypatch, tmp_path):
    actor_token_file = tmp_path / "actor-token"
    actor_token_file.write_text("actor-jwt-value\n")

    calls: list = []
    responses = [
        _FakeResponse(200, {"auth_req_id": "abc", "interval": 5}),
        _FakeResponse(200, {"access_token": "final-jwt"}),
    ]
    _patch_client(monkeypatch, calls, responses)

    client = CibaClient(
        keycloak_url="http://keycloak.local",
        realm="demo",
        client_id="ciba-client",
        client_secret="secret",
        actor_token_path=str(actor_token_file),
    )

    token = await client.fetch_access_token(login_hint="admin", binding_message="create_user")

    assert token == "final-jwt"
    poll_url, poll_data = calls[1]
    assert poll_data["delegation_actor"] == "actor-jwt-value"


async def test_delegation_actor_omitted_when_actor_token_missing(monkeypatch, tmp_path):
    missing_path = tmp_path / "absent-actor-token"

    calls: list = []
    responses = [
        _FakeResponse(200, {"auth_req_id": "abc", "interval": 5}),
        _FakeResponse(200, {"access_token": "final-jwt"}),
    ]
    _patch_client(monkeypatch, calls, responses)

    client = CibaClient(
        keycloak_url="http://keycloak.local",
        realm="demo",
        client_id="ciba-client",
        client_secret="secret",
        actor_token_path=str(missing_path),
    )

    token = await client.fetch_access_token(login_hint="admin", binding_message="create_user")

    assert token == "final-jwt"
    poll_url, poll_data = calls[1]
    assert "delegation_actor" not in poll_data


async def test_delegation_actor_omitted_when_not_configured(monkeypatch, tmp_path):
    calls: list = []
    responses = [
        _FakeResponse(200, {"auth_req_id": "abc", "interval": 5}),
        _FakeResponse(200, {"access_token": "final-jwt"}),
    ]
    _patch_client(monkeypatch, calls, responses)

    client = CibaClient(
        keycloak_url="http://keycloak.local",
        realm="demo",
        client_id="ciba-client",
        client_secret="secret",
    )

    await client.fetch_access_token(login_hint="admin", binding_message="create_user")

    poll_url, poll_data = calls[1]
    assert "delegation_actor" not in poll_data
