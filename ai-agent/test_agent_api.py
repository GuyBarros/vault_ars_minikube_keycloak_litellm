import asyncio
import base64
import json
import logging
import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("LANGCHAIN_MODEL", "openai:gpt-5-mini")

import agent_api
import identity
import agent_runtime
import tools
from agent_runtime import SYSTEM_PROMPT, AgentRuntime
from identity import OboTokenService


def test_system_prompt_treats_create_user_as_blocking_ciba_write():
    # Regression: leftover IBM Verify device-grant copy made the model ask
    # for a verification link / confirm_user_creation after CIBA already
    # created the user.
    assert "confirm_user_creation" not in SYSTEM_PROMPT
    assert "device_code" not in SYSTEM_PROMPT
    assert "does NOT create the user" not in SYSTEM_PROMPT
    assert "CIBA" in SYSTEM_PROMPT
    assert "create_user" in SYSTEM_PROMPT


class FakeChunk:
    def __init__(self, content: str):
        self.content = content


class FakeAssistantResponse:
    def __init__(self, tool_calls=None, content=""):
        self.tool_calls = tool_calls or []
        self.content = content


class FakeToolBoundLLM:
    def __init__(self, response=None, responses=None, stream_chunks=None):
        if responses is not None:
            self.responses = list(responses)
        elif response is not None:
            self.responses = [response]
        else:
            self.responses = []
        self.invoke_count = 0
        self.stream_chunks = stream_chunks or [FakeChunk("streamed output")]
        self.last_stream_messages = None

    def invoke(self, messages):
        if self.invoke_count < len(self.responses):
            response = self.responses[self.invoke_count]
        else:
            response = FakeAssistantResponse()
        self.invoke_count += 1
        return response

    def stream(self, messages):
        self.last_stream_messages = list(messages)
        return iter(self.stream_chunks)


class FakeTool:
    def __init__(self, output: str):
        self.output = output
        self.invocations: list[dict] = []

    def invoke(self, args):
        self.invocations.append(args)
        return self.output

    async def ainvoke(self, args):
        self.invocations.append(args)
        return self.output


class FakeBoundLLM:
    def __init__(self, bound: "FakeToolBoundLLM | None" = None):
        # When *bound* is provided, every bind_tools call returns that exact
        # instance (tests inspect its state). Otherwise a fresh
        # FakeToolBoundLLM is minted per bind_tools call so request state
        # doesn't leak across tests when the runtime is built per-request.
        self._fixed = bound
        self.bound: "FakeToolBoundLLM | None" = bound
        self.last_bound_tool_names: list[str] = []

    def bind_tools(self, tools):
        self.last_bound_tool_names = [getattr(t, "name", None) for t in tools]
        if self._fixed is not None:
            self.bound = self._fixed
        else:
            self.bound = FakeToolBoundLLM()
        return self.bound


def _jwt_with_expiry(offset_seconds: int) -> str:
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "none", "typ": "JWT"}, separators=(",", ":")).encode(
            "utf-8"
        )
    ).rstrip(b"=")
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {"sub": "user-123", "exp": int(time.time()) + offset_seconds},
            separators=(",", ":"),
        ).encode("utf-8")
    ).rstrip(b"=")
    signature = b"signature"
    return b".".join([header, payload, signature]).decode("utf-8")


def _jwt_with_claims(offset_seconds: int, **extra_claims) -> str:
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "none", "typ": "JWT"}, separators=(",", ":")).encode(
            "utf-8"
        )
    ).rstrip(b"=")
    claims = {"sub": "user-123", "exp": int(time.time()) + offset_seconds}
    claims.update(extra_claims)
    payload = base64.urlsafe_b64encode(
        json.dumps(claims, separators=(",", ":")).encode("utf-8")
    ).rstrip(b"=")
    signature = b"signature"
    return b".".join([header, payload, signature]).decode("utf-8")


def _structured_log_records(caplog, logger_name: str):
    structured_logs = []
    for record in caplog.records:
        if record.name != logger_name:
            continue
        try:
            structured_logs.append(json.loads(record.getMessage()))
        except json.JSONDecodeError:
            continue
    return structured_logs


@pytest.fixture(autouse=True)
def isolate_runtime(tmp_path, monkeypatch):
    actor_token_path = tmp_path / "actor-token"
    actor_token_path.write_text("actor-token", encoding="utf-8")

    monkeypatch.setattr(agent_api.SETTINGS, "actor_token_path", actor_token_path)
    monkeypatch.setattr(agent_api.SETTINGS, "token_exchange_url", "http://token-exchange.local/obo")
    monkeypatch.setattr(agent_api.SETTINGS, "obo_role_name", "test-role")
    monkeypatch.setattr(agent_api.SETTINGS, "bypass_auth_token_exchange", False)

    agent_api.app.state.settings = agent_api.SETTINGS
    # Default: runtime is built per request (None signals build-on-demand).
    # Tests that need a custom tool registry override this directly.
    agent_api.app.state.agent_runtime = None
    agent_api.app.state.llm = FakeBoundLLM()

    # Discovery happens once at startup; in tests we never run lifespan, so
    # seed the cached templates as empty. Tests that need MCP tools can set
    # this directly on app.state.
    agent_api.app.state.mcp_template_tools = []

    agent_api.app.state.token_service = OboTokenService(
        settings=agent_api.SETTINGS,
        logger=agent_api.LOGGER,
    )
    agent_api.app.state.token_service.clear_cache()
    agent_api.app.state.actor_agent_id = agent_api._load_startup_agent_id(
        agent_api.app.state.token_service
    )


def test_agent_card_is_served_without_auth_at_all_well_known_paths():
    client = TestClient(agent_api.app, base_url="http://ai-agent.virtual.consul:8000")

    for path in ("/.well-known/agent-card.json", "/.well-known/agent.json", "/agent.json"):
        response = client.get(path)
        assert response.status_code == 200, path
        card = response.json()
        assert card["name"] == "ai-agent"
        assert card["url"] == "http://ai-agent.virtual.consul:8000"


def test_missing_bearer_token_is_rejected():
    client = TestClient(agent_api.app)

    response = client.post(
        "/v1/agent/query",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 401
    assert response.json() == {
        "error": "invalid_request",
        "message": "Authorization bearer token is required.",
    }


def test_expired_bearer_token_is_rejected(monkeypatch):
    client = TestClient(agent_api.app)
    expired_token = _jwt_with_expiry(-30)

    def fail_exchange(*args, **kwargs):
        raise AssertionError("Token exchange should not run for expired access tokens.")

    monkeypatch.setattr(agent_api.app.state.token_service, "perform_token_exchange", fail_exchange)

    response = client.post(
        "/v1/agent/query",
        headers={"Authorization": f"Bearer {expired_token}"},
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 401
    assert response.json() == {
        "error": "invalid_token",
        "message": "Bearer token has expired.",
    }


def test_bypass_mode_allows_request_without_authorization(monkeypatch):
    client = TestClient(agent_api.app)
    monkeypatch.setattr(agent_api.app.state.settings, "bypass_auth_token_exchange", True)

    def fail_exchange(*args, **kwargs):
        raise AssertionError("Token exchange should not run when bypass mode is enabled.")

    monkeypatch.setattr(agent_api.app.state.token_service, "resolve_token", fail_exchange)

    response = client.post(
        "/v1/agent/query",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 200
    assert response.text == "streamed output"


def test_bypass_mode_ignores_invalid_authorization_header(monkeypatch):
    client = TestClient(agent_api.app)
    monkeypatch.setattr(agent_api.app.state.settings, "bypass_auth_token_exchange", True)

    def fail_exchange(*args, **kwargs):
        raise AssertionError("Token exchange should not run when bypass mode is enabled.")

    monkeypatch.setattr(agent_api.app.state.token_service, "resolve_token", fail_exchange)

    response = client.post(
        "/v1/agent/query",
        headers={"Authorization": "Basic not-a-bearer-token"},
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 200
    assert response.text == "streamed output"


def test_old_chat_route_is_removed():
    client = TestClient(agent_api.app)

    response = client.post("/chat", json={"messages": []})

    assert response.status_code == 404


def test_request_id_header_is_preserved_and_propagated(monkeypatch):
    client = TestClient(agent_api.app)
    access_token = _jwt_with_expiry(300)
    request_id = "request-from-header"

    response = client.post(
        "/v1/agent/query",
        headers={
            "Authorization": f"Bearer {access_token}",
            "X-Request-ID": request_id,
        },
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == request_id


def test_request_id_is_generated_when_header_is_missing(monkeypatch):
    client = TestClient(agent_api.app)
    access_token = _jwt_with_expiry(300)

    monkeypatch.setattr(
        agent_api.app.state.token_service,
        "perform_token_exchange",
        lambda subject_token, actor_token, request_id, scope: (_jwt_with_expiry(600), time.time() + 600),
    )

    response = client.post(
        "/v1/agent/query",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"]
    assert str(uuid.UUID(response.headers["X-Request-ID"])) == response.headers["X-Request-ID"]


def test_obo_token_is_cached_between_requests(monkeypatch):
    """Two resolve_token calls for the same scope set hit the cache once.

    Per-call OBO exchange now happens inside the scoped-tool wrapper rather
    than at request entry, so we exercise the cache directly via the token
    service — the surface that scoped-tool wrappers depend on.
    """
    access_token = _jwt_with_expiry(300)
    obo_token = _jwt_with_expiry(600)
    exchange_calls: list[str] = []

    def fake_exchange(subject_token, actor_token, request_id, scope):
        exchange_calls.append(scope)
        return obo_token, time.time() + 600

    monkeypatch.setattr(agent_api.app.state.token_service, "perform_token_exchange", fake_exchange)

    first = agent_api.app.state.token_service.resolve_token(
        subject_token=access_token, request_id="r1", scopes=["users.read"]
    )
    second = agent_api.app.state.token_service.resolve_token(
        subject_token=access_token, request_id="r2", scopes=["users.read"]
    )

    assert first == second == obo_token
    assert exchange_calls == ["users.read"]


def test_cached_tokens_can_be_retrieved_via_endpoint(monkeypatch):
    """/v1/agent/tokens returns the most recently exchanged OBO once seeded."""
    client = TestClient(agent_api.app)
    access_token = _jwt_with_expiry(300)
    obo_token = _jwt_with_expiry(600)

    def fake_exchange(subject_token, actor_token, request_id, scope):
        return obo_token, time.time() + 600

    monkeypatch.setattr(agent_api.app.state.token_service, "perform_token_exchange", fake_exchange)

    agent_api.app.state.token_service.resolve_token(
        subject_token=access_token,
        request_id="seed",
        scopes=["users.read"],
    )

    token_response = client.get(
        "/v1/agent/tokens",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert token_response.status_code == 200
    assert token_response.json() == {
        "obo_token": obo_token,
        "actor_token": "actor-token",
    }


def test_assurance_endpoint_returns_empty_when_nothing_recorded():
    client = TestClient(agent_api.app)
    access_token = _jwt_with_expiry(300)

    response = client.get(
        "/v1/agent/assurance",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "tool": None,
        "decision": None,
        "current_loa": None,
        "required_loa": None,
    }


def test_assurance_endpoint_returns_last_recorded_decision():
    client = TestClient(agent_api.app)
    access_token = _jwt_with_expiry(300)
    assurance = {
        "tool": "create_user",
        "decision": "ALLOW",
        "current_loa": 2,
        "required_loa": 2,
    }
    agent_api.app.state.assurance_tracker.record(access_token, assurance)

    response = client.get(
        "/v1/agent/assurance",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    assert response.json() == assurance


def test_tokens_endpoint_returns_latest_after_multi_scope_turn(monkeypatch):
    """When a turn exchanges read then write, /v1/agent/tokens surfaces write."""
    client = TestClient(agent_api.app)
    access_token = _jwt_with_expiry(300)
    read_obo = _jwt_with_expiry(600)
    write_obo = _jwt_with_expiry(700)

    tokens_by_scope = {"users.read": read_obo, "users.write": write_obo}

    def fake_exchange(subject_token, actor_token, request_id, scope):
        return tokens_by_scope[scope], time.time() + 600

    monkeypatch.setattr(agent_api.app.state.token_service, "perform_token_exchange", fake_exchange)

    service = agent_api.app.state.token_service
    service.resolve_token(subject_token=access_token, request_id="r1", scopes=["users.read"])
    service.resolve_token(subject_token=access_token, request_id="r2", scopes=["users.write"])

    response = client.get(
        "/v1/agent/tokens",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    assert response.json()["obo_token"] == write_obo


def test_get_last_obo_pointer_flips_back_on_cache_hit_for_earlier_scope(monkeypatch):
    """A re-request of users.read after users.write must flip the pointer back."""
    access_token = _jwt_with_expiry(300)
    read_obo = _jwt_with_expiry(600)
    write_obo = _jwt_with_expiry(700)
    tokens_by_scope = {"users.read": read_obo, "users.write": write_obo}

    def fake_exchange(subject_token, actor_token, request_id, scope):
        return tokens_by_scope[scope], time.time() + 600

    monkeypatch.setattr(agent_api.app.state.token_service, "perform_token_exchange", fake_exchange)

    service = agent_api.app.state.token_service
    service.resolve_token(subject_token=access_token, request_id="r1", scopes=["users.read"])
    service.resolve_token(subject_token=access_token, request_id="r2", scopes=["users.write"])
    assert service.get_last_obo_token(access_token) == write_obo

    # Cache-hit re-request of users.read — the pointer must follow this call.
    service.resolve_token(subject_token=access_token, request_id="r3", scopes=["users.read"])
    assert service.get_last_obo_token(access_token) == read_obo


def test_get_last_obo_returns_none_for_unknown_subject():
    service = agent_api.app.state.token_service
    assert service.get_last_obo_token("never-exchanged-token") is None


def test_get_last_obo_returns_none_after_expiry(monkeypatch):
    """Expired pointer entries must be cleaned up and return None."""
    access_token = _jwt_with_expiry(300)
    obo_token = _jwt_with_expiry(600)

    def fake_exchange(subject_token, actor_token, request_id, scope):
        # Exchange returns a token that's already past its expiry.
        return obo_token, time.time() - 1

    monkeypatch.setattr(agent_api.app.state.token_service, "perform_token_exchange", fake_exchange)

    service = agent_api.app.state.token_service
    # resolve_token still returns the token (expiry isn't checked at write
    # time), but the cache entry is past its TTL so the next read evicts it.
    service.resolve_token(subject_token=access_token, request_id="r1", scopes=["users.read"])

    assert service.get_last_obo_token(access_token) is None
    # And the pointer was cleaned up so a second call still returns None.
    assert access_token not in service.last_cache_key_by_subject


def test_tokens_endpoint_returns_actor_with_null_obo_on_cache_miss(monkeypatch):
    """Cache miss must not block the actor token from being surfaced."""
    client = TestClient(agent_api.app)
    access_token = _jwt_with_expiry(300)

    def fail_exchange(*args, **kwargs):
        raise AssertionError("Token exchange should not run for cache lookup endpoint.")

    monkeypatch.setattr(agent_api.app.state.token_service, "perform_token_exchange", fail_exchange)

    response = client.get(
        "/v1/agent/tokens",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "actor_token": "actor-token",
        "obo_token": None,
    }


def test_tokens_endpoint_returns_actor_in_bypass_mode(monkeypatch):
    """Bypass mode also returns the actor token; OBO is null."""
    client = TestClient(agent_api.app)
    monkeypatch.setattr(agent_api.app.state.settings, "bypass_auth_token_exchange", True)

    response = client.get("/v1/agent/tokens")

    assert response.status_code == 200
    assert response.json() == {
        "actor_token": "actor-token",
        "obo_token": None,
    }


def test_token_exchange_posts_subject_and_actor_tokens(monkeypatch, caplog):
    access_token = _jwt_with_expiry(300)
    captured = {}

    class FakeHTTPResponse:
        def __init__(self, body: str):
            self.body = body.encode("utf-8")

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["headers"] = {key.lower(): value for key, value in request.header_items()}
        captured["timeout"] = timeout
        return FakeHTTPResponse(
            json.dumps({"access_token": _jwt_with_expiry(300), "expires_in": 300})
        )

    monkeypatch.setattr(identity.urllib.request, "urlopen", fake_urlopen)

    with caplog.at_level(logging.INFO, logger="agent_api"):
        obo_token, expiry_time = identity.perform_token_exchange(
            subject_token=access_token,
            actor_token="actor-token",
            settings=agent_api.SETTINGS,
            logger=agent_api.LOGGER,
            request_id="request-1",
            scope="users.read",
        )

    assert obo_token
    assert expiry_time > time.time()
    assert captured == {
        "url": agent_api.SETTINGS.token_exchange_url,
        "payload": {
            "subject_token": access_token,
            "actor_token": "actor-token",
            "scope": "users.read",
        },
        "headers": {
            "accept": "application/json",
            "content-type": "application/json",
            "x-request-id": "request-1",
        },
        "timeout": agent_api.SETTINGS.token_exchange_timeout_seconds,
    }
    completion_logs = [
        payload
        for payload in _structured_log_records(caplog, "agent_api")
        if payload.get("event") == "obo_token_exchange_completed"
    ]
    assert completion_logs
    assert completion_logs[-1]["obo_token_present"] is True
    assert "obo_token" not in completion_logs[-1]


def test_read_request_does_not_trigger_fallback_shell(monkeypatch):
    client = TestClient(agent_api.app)
    access_token = _jwt_with_expiry(300)
    bound_llm = FakeToolBoundLLM()
    agent_api.app.state.agent_runtime = AgentRuntime(
        llm_with_tools=bound_llm,
        logger=agent_api.LOGGER,
    )

    monkeypatch.setattr(
        agent_api.app.state.token_service,
        "perform_token_exchange",
        lambda subject_token, actor_token, request_id, scope: (_jwt_with_expiry(600), time.time() + 600),
    )

    response = client.post(
        "/v1/agent/query",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"messages": [{"role": "user", "content": "read /tmp/example.txt"}]},
    )

    assert response.status_code == 200
    assert response.text == "streamed output"
    assert bound_llm.last_stream_messages is not None
    assert len(bound_llm.last_stream_messages) == 2


def test_streaming_response_is_logged_with_agent_text(monkeypatch, caplog):
    client = TestClient(agent_api.app)
    access_token = _jwt_with_expiry(300)

    monkeypatch.setattr(
        agent_api.app.state.token_service,
        "perform_token_exchange",
        lambda subject_token, actor_token, request_id, scope: (_jwt_with_expiry(600), time.time() + 600),
    )

    with caplog.at_level(logging.INFO, logger="agent_api"):
        response = client.post(
            "/v1/agent/query",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"messages": [{"role": "user", "content": "hello"}]},
        )

    assert response.status_code == 200
    assert response.text == "streamed output"

    response_logs = [
        payload
        for payload in _structured_log_records(caplog, "agent_api")
        if payload.get("event") == "response_sent"
    ]
    assert response_logs
    assert response_logs[-1]["response_text"] == "streamed output"
    assert response_logs[-1]["level"] == "INFO"
    assert response_logs[-1]["logger"] == "agent_api"
    assert response_logs[-1]["hostname"]
    assert "host_ip" in response_logs[-1]
    assert response_logs[-1]["module"] == "agent_runtime"
    assert response_logs[-1]["function"] == "_stream_text_chunks"
    assert response_logs[-1]["method_name"] == "_stream_text_chunks"
    assert isinstance(response_logs[-1]["line_number"], int)
    assert response_logs[-1]["http_method"] == "POST"
    assert response_logs[-1]["client_ip"] == "testclient"


def test_multi_round_tool_calls_are_dispatched_in_sequence(monkeypatch, caplog):
    client = TestClient(agent_api.app)
    access_token = _jwt_with_expiry(300)

    search_tool = FakeTool('[{"email": "mia.williams670@example.com", "first_name": "Mia"}]')
    search_tool.name = "search_users_by_first_name"
    update_tool = FakeTool('{"email": "mia.changed@example.com", "updated": true}')
    update_tool.name = "update_user_by_email"

    bound_llm = FakeToolBoundLLM(
        responses=[
            FakeAssistantResponse(
                tool_calls=[
                    {
                        "id": "call_search_1",
                        "name": "search_users_by_first_name",
                        "args": {"first_name": "Mia"},
                    }
                ],
            ),
            FakeAssistantResponse(
                tool_calls=[
                    {
                        "id": "call_update_1",
                        "name": "update_user_by_email",
                        "args": {
                            "current_email": "mia.williams670@example.com",
                            "email": "mia.changed@example.com",
                        },
                    }
                ],
            ),
            FakeAssistantResponse(),
        ],
        stream_chunks=[FakeChunk("Done. Mia's email has been updated.")],
    )
    agent_api.app.state.agent_runtime = AgentRuntime(
        llm_with_tools=bound_llm,
        logger=agent_api.LOGGER,
        tool_registry={
            "search_users_by_first_name": search_tool,
            "update_user_by_email": update_tool,
        },
    )

    monkeypatch.setattr(
        agent_api.app.state.token_service,
        "perform_token_exchange",
        lambda subject_token, actor_token, request_id, scope: (_jwt_with_expiry(600), time.time() + 600),
    )

    with caplog.at_level(logging.INFO, logger="agent_api"):
        response = client.post(
            "/v1/agent/query",
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "messages": [
                    {
                        "role": "user",
                        "content": "update mia's email to mia.changed@example.com",
                    }
                ]
            },
        )

    assert response.status_code == 200
    assert response.text == "Done. Mia's email has been updated."
    assert search_tool.invocations == [{"first_name": "Mia"}]
    assert update_tool.invocations == [
        {
            "current_email": "mia.williams670@example.com",
            "email": "mia.changed@example.com",
        }
    ]
    assert bound_llm.invoke_count == 3
    assert bound_llm.last_stream_messages is not None
    assert len(bound_llm.last_stream_messages) == 6

    info_stages = {
        (payload.get("event"), payload.get("stage"))
        for payload in _structured_log_records(caplog, "agent_api")
    }
    assert ("agent_execution", "tool_call") not in info_stages
    assert ("agent_execution", "tool_result") not in info_stages


def test_non_shell_tool_call_is_executed_through_runtime_registry(monkeypatch):
    client = TestClient(agent_api.app)
    access_token = _jwt_with_expiry(300)
    fake_user_tool = FakeTool('[{"email": "noah.thompson206@example.com"}]')
    fake_user_tool.name = "list_all_users"
    bound_llm = FakeToolBoundLLM(
        responses=[
            FakeAssistantResponse(
                tool_calls=[
                    {
                        "id": "call_users_1",
                        "name": "list_all_users",
                        "args": {},
                    }
                ]
            ),
            FakeAssistantResponse(),
        ]
    )
    agent_api.app.state.agent_runtime = AgentRuntime(
        llm_with_tools=bound_llm,
        logger=agent_api.LOGGER,
        tool_registry={"list_all_users": fake_user_tool},
    )

    monkeypatch.setattr(
        agent_api.app.state.token_service,
        "perform_token_exchange",
        lambda subject_token, actor_token, request_id, scope: (_jwt_with_expiry(600), time.time() + 600),
    )

    response = client.post(
        "/v1/agent/query",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"messages": [{"role": "user", "content": "list all users"}]},
    )

    assert response.status_code == 200
    assert bound_llm.last_stream_messages is not None
    assert len(bound_llm.last_stream_messages) == 4
    assert (
        json.loads(bound_llm.last_stream_messages[-1].content)[0]["email"]
        == "noah.thompson206@example.com"
    )


def test_shell_tool_result_is_logged(monkeypatch, caplog):
    client = TestClient(agent_api.app)
    access_token = _jwt_with_expiry(300)
    agent_api.app.state.agent_runtime = AgentRuntime(
        llm_with_tools=FakeToolBoundLLM(
            responses=[
                FakeAssistantResponse(
                    tool_calls=[
                        {
                            "id": "call_shell_1",
                            "name": "shell",
                            "args": {"command": "ls -l agent_runtime.py"},
                        }
                    ]
                ),
                FakeAssistantResponse(),
            ]
        ),
        logger=agent_api.LOGGER,
    )

    monkeypatch.setattr(
        agent_api.app.state.token_service,
        "perform_token_exchange",
        lambda subject_token, actor_token, request_id, scope: (_jwt_with_expiry(600), time.time() + 600),
    )
    fake_shell = FakeTool(
        "exit_code: 0\nstdout:\n-rw-r--r-- agent_runtime.py\nstderr:\n"
    )
    fake_shell.name = "shell"
    agent_api.app.state.agent_runtime.tool_registry["shell"] = fake_shell

    with caplog.at_level(logging.DEBUG, logger="agent_api"):
        response = client.post(
            "/v1/agent/query",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"messages": [{"role": "user", "content": "show me the file details"}]},
        )

    assert response.status_code == 200

    tool_result_logs = [
        payload
        for payload in _structured_log_records(caplog, "agent_api")
        if payload.get("stage") == "tool_result"
    ]
    assert tool_result_logs
    assert tool_result_logs[-1]["level"] == "DEBUG"
    assert "message" not in tool_result_logs[-1]
    assert tool_result_logs[-1]["tool_name"] == "shell"
    assert tool_result_logs[-1]["exit_code"] == 0
    assert tool_result_logs[-1]["stdout_length"] > 0
    assert tool_result_logs[-1]["stderr_length"] == 0
    assert tool_result_logs[-1]["request_id"]
    assert tool_result_logs[-1]["http_method"] == "POST"
    assert tool_result_logs[-1]["client_ip"] == "testclient"


def test_create_app_builds_runtime_from_configured_model(monkeypatch):
    captured = {}

    def fake_init_chat_model(model, **kwargs):
        captured["model"] = model
        captured["kwargs"] = kwargs
        return FakeBoundLLM()

    monkeypatch.setattr(agent_api, "init_chat_model", fake_init_chat_model)

    app = agent_api.create_app(
        settings=agent_api.Settings(
            model="openai:gpt-5.4-mini",
            ollama_base_url=agent_api.SETTINGS.ollama_base_url,
            litellm_base_url=agent_api.SETTINGS.litellm_base_url,
            actor_token_path=agent_api.SETTINGS.actor_token_path,
            token_exchange_url=agent_api.SETTINGS.token_exchange_url,
            token_exchange_timeout_seconds=agent_api.SETTINGS.token_exchange_timeout_seconds,
            obo_role_name=agent_api.SETTINGS.obo_role_name,
            bypass_auth_token_exchange=agent_api.SETTINGS.bypass_auth_token_exchange,
            host=agent_api.SETTINGS.host,
            port=agent_api.SETTINGS.port,
            log_level=agent_api.SETTINGS.log_level,
            user_mcp_url=agent_api.SETTINGS.user_mcp_url,
            mcp_tool_call_timeout_seconds=agent_api.SETTINGS.mcp_tool_call_timeout_seconds,
        )
    )

    assert captured == {
        "model": "openai:gpt-5.4-mini",
        "kwargs": {"streaming": True},
    }
    assert app.state.llm is not None
    # In production, agent_runtime is built per-request and stays None at startup.
    assert app.state.agent_runtime is None


def test_create_app_routes_through_litellm_gateway_when_configured(monkeypatch):
    captured = {}

    def fake_init_chat_model(model, **kwargs):
        captured["model"] = model
        captured["kwargs"] = kwargs
        return FakeBoundLLM()

    monkeypatch.setattr(agent_api, "init_chat_model", fake_init_chat_model)

    agent_api.create_app(
        settings=agent_api.Settings(
            model="openai:qwen-local",
            ollama_base_url=agent_api.SETTINGS.ollama_base_url,
            litellm_base_url="http://litellm-gateway.default.svc.cluster.local:4000/v1",
            actor_token_path=agent_api.SETTINGS.actor_token_path,
            token_exchange_url=agent_api.SETTINGS.token_exchange_url,
            token_exchange_timeout_seconds=agent_api.SETTINGS.token_exchange_timeout_seconds,
            obo_role_name=agent_api.SETTINGS.obo_role_name,
            bypass_auth_token_exchange=agent_api.SETTINGS.bypass_auth_token_exchange,
            host=agent_api.SETTINGS.host,
            port=agent_api.SETTINGS.port,
            log_level=agent_api.SETTINGS.log_level,
            user_mcp_url=agent_api.SETTINGS.user_mcp_url,
            mcp_tool_call_timeout_seconds=agent_api.SETTINGS.mcp_tool_call_timeout_seconds,
        )
    )

    assert captured == {
        "model": "openai:qwen-local",
        "kwargs": {
            "streaming": True,
            "base_url": "http://litellm-gateway.default.svc.cluster.local:4000/v1",
            "api_key": "sk-litellm-local",
        },
    }


def test_agent_request_started_is_logged_with_identity(monkeypatch, caplog):
    client = TestClient(agent_api.app)
    access_token = _jwt_with_claims(300, preferred_username="alice@example.com")
    actor_token = _jwt_with_claims(600, agent_id="agent-42")
    agent_api.SETTINGS.actor_token_path.write_text(actor_token, encoding="utf-8")
    agent_api.app.state.actor_agent_id = agent_api._load_startup_agent_id(
        agent_api.app.state.token_service
    )
    obo_token = _jwt_with_expiry(600)

    monkeypatch.setattr(
        agent_api.app.state.token_service,
        "perform_token_exchange",
        lambda subject_token, actor_token, request_id, scope: (obo_token, time.time() + 600),
    )

    with caplog.at_level(logging.INFO, logger="agent_api"):
        response = client.post(
            "/v1/agent/query",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"messages": [{"role": "user", "content": "hello there"}]},
        )

    assert response.status_code == 200

    started_logs = [
        payload
        for payload in _structured_log_records(caplog, "agent_api")
        if payload.get("event") == "agent_request_started"
    ]
    assert started_logs
    assert started_logs[-1]["level"] == "INFO"
    assert started_logs[-1]["message"] == (
        "[user=alice@example.com agent=agent-42] "
        "Agent started processing user request"
    )
    assert "preferred_username" not in started_logs[-1]
    assert "actor_agent_id" not in started_logs[-1]
    assert started_logs[-1]["user_message"] == "hello there"


def test_response_sent_includes_identity_and_user_message(monkeypatch, caplog):
    client = TestClient(agent_api.app)
    access_token = _jwt_with_claims(300, preferred_username="bob@example.com")
    actor_token = _jwt_with_claims(600, agent_id="agent-99")
    agent_api.SETTINGS.actor_token_path.write_text(actor_token, encoding="utf-8")
    agent_api.app.state.actor_agent_id = agent_api._load_startup_agent_id(
        agent_api.app.state.token_service
    )
    obo_token = _jwt_with_expiry(600)

    monkeypatch.setattr(
        agent_api.app.state.token_service,
        "perform_token_exchange",
        lambda subject_token, actor_token, request_id, scope: (obo_token, time.time() + 600),
    )

    with caplog.at_level(logging.INFO, logger="agent_api"):
        response = client.post(
            "/v1/agent/query",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"messages": [{"role": "user", "content": "what is up"}]},
        )

    assert response.status_code == 200

    response_logs = [
        payload
        for payload in _structured_log_records(caplog, "agent_api")
        if payload.get("event") == "response_sent"
    ]
    assert response_logs
    assert response_logs[-1]["level"] == "INFO"
    assert response_logs[-1]["message"] == (
        "[user=bob@example.com agent=agent-99] "
        "Agent finished processing user request"
    )
    assert "preferred_username" not in response_logs[-1]
    assert "actor_agent_id" not in response_logs[-1]
    assert response_logs[-1]["user_message"] == "what is up"


def test_bypass_mode_logs_have_no_identity_prefix(monkeypatch, caplog):
    client = TestClient(agent_api.app)
    monkeypatch.setattr(agent_api.app.state.settings, "bypass_auth_token_exchange", True)

    def fail_exchange(*args, **kwargs):
        raise AssertionError("Token exchange should not run when bypass mode is enabled.")

    monkeypatch.setattr(agent_api.app.state.token_service, "resolve_token", fail_exchange)

    with caplog.at_level(logging.INFO, logger="agent_api"):
        response = client.post(
            "/v1/agent/query",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )

    assert response.status_code == 200

    post_exchange_logs = [
        payload
        for payload in _structured_log_records(caplog, "agent_api")
        if payload.get("event") in {"agent_request_started", "response_sent"}
    ]
    assert post_exchange_logs
    for payload in post_exchange_logs:
        assert not payload["message"].startswith("[")
        assert "preferred_username" not in payload
        assert "actor_agent_id" not in payload


def test_demoted_events_are_not_emitted_at_info(monkeypatch, caplog):
    client = TestClient(agent_api.app)
    access_token = _jwt_with_expiry(300)

    monkeypatch.setattr(
        agent_api.app.state.token_service,
        "perform_token_exchange",
        lambda subject_token, actor_token, request_id, scope: (_jwt_with_expiry(600), time.time() + 600),
    )

    with caplog.at_level(logging.INFO, logger="agent_api"):
        first = client.post(
            "/v1/agent/query",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"messages": [{"role": "user", "content": "hello"}]},
        )
        second = client.post(
            "/v1/agent/query",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"messages": [{"role": "user", "content": "again"}]},
        )

    assert first.status_code == 200
    assert second.status_code == 200

    info_records = _structured_log_records(caplog, "agent_api")
    info_events = {payload.get("event") for payload in info_records}
    info_stages = {
        (payload.get("event"), payload.get("stage"))
        for payload in info_records
    }
    assert "request_received" not in info_events
    assert "actor_token_path_used" not in info_events
    assert "token_cache_hit" not in info_events
    assert "token_cache_miss" not in info_events
    assert ("agent_execution", "invoke") not in info_stages


def _refresh_actor_agent_id(jwt_actor_token: str) -> None:
    agent_api.SETTINGS.actor_token_path.write_text(jwt_actor_token, encoding="utf-8")
    agent_api.app.state.actor_agent_id = agent_api._load_startup_agent_id(
        agent_api.app.state.token_service
    )


def test_request_received_log_includes_agent_prefix(caplog):
    _refresh_actor_agent_id(_jwt_with_claims(600, agent_id="agent-mw"))
    client = TestClient(agent_api.app)

    with caplog.at_level(logging.DEBUG, logger="agent_api"):
        client.get("/v1/agent/tokens")

    received_logs = [
        payload
        for payload in _structured_log_records(caplog, "agent_api")
        if payload.get("event") == "request_received"
    ]
    assert received_logs
    assert received_logs[-1]["message"].startswith("[agent=agent-mw]")
    assert "actor_agent_id" not in received_logs[-1]


def test_request_failed_log_includes_agent_prefix(caplog):
    _refresh_actor_agent_id(_jwt_with_claims(600, agent_id="agent-err"))
    client = TestClient(agent_api.app)

    with caplog.at_level(logging.INFO, logger="agent_api"):
        response = client.post(
            "/v1/agent/query",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )

    assert response.status_code == 401
    failed_logs = [
        payload
        for payload in _structured_log_records(caplog, "agent_api")
        if payload.get("event") == "request_failed"
    ]
    assert failed_logs
    assert failed_logs[-1]["message"].startswith("[agent=agent-err]")


def test_response_sent_for_non_query_endpoint_includes_agent_prefix(caplog):
    _refresh_actor_agent_id(_jwt_with_claims(600, agent_id="agent-tokens"))
    client = TestClient(agent_api.app)

    with caplog.at_level(logging.DEBUG, logger="agent_api"):
        client.get("/v1/agent/tokens")

    response_logs = [
        payload
        for payload in _structured_log_records(caplog, "agent_api")
        if payload.get("event") == "response_sent"
    ]
    assert response_logs
    assert response_logs[-1]["message"].startswith("[agent=agent-tokens]")


def test_load_startup_agent_id_returns_none_when_actor_token_missing(tmp_path, monkeypatch):
    missing_path = tmp_path / "absent-actor-token"
    monkeypatch.setattr(agent_api.SETTINGS, "actor_token_path", missing_path)
    service = OboTokenService(settings=agent_api.SETTINGS, logger=agent_api.LOGGER)

    assert agent_api._load_startup_agent_id(service) is None


def _run(coro):
    return asyncio.run(coro)


def test_discover_mcp_tools_retries_then_succeeds(monkeypatch):
    # A rolling deploy routinely starts ai-agent before user-mcp (and its mesh
    # sidecars) is ready to serve it - the first two attempts here stand in
    # for that race, and the third simulates user-mcp coming up in time.
    attempts = []

    async def fake_fetch(user_mcp_url, obo_token, request_id, timeout_seconds, **_kwargs):
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("connection refused")
        return ["tool-a", "tool-b"]

    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(agent_api, "fetch_mcp_tools", fake_fetch)
    monkeypatch.setattr(agent_api.asyncio, "sleep", fake_sleep)

    result = _run(
        agent_api._discover_mcp_template_tools_at_startup(
            "http://user-mcp.virtual.consul/mcp", max_attempts=5, initial_backoff_seconds=1.0
        )
    )

    assert result == ["tool-a", "tool-b"]
    assert len(attempts) == 3
    assert sleeps == [1.0, 2.0]


def test_discover_mcp_tools_gives_up_after_max_attempts(monkeypatch):
    async def always_fails(user_mcp_url, obo_token, request_id, timeout_seconds, **_kwargs):
        raise RuntimeError("connection refused")

    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(agent_api, "fetch_mcp_tools", always_fails)
    monkeypatch.setattr(agent_api.asyncio, "sleep", fake_sleep)

    result = _run(
        agent_api._discover_mcp_template_tools_at_startup(
            "http://user-mcp.virtual.consul/mcp", max_attempts=3, initial_backoff_seconds=1.0
        )
    )

    assert result == []
    # Backoff happens between attempts, never after the last one.
    assert sleeps == [1.0, 2.0]
