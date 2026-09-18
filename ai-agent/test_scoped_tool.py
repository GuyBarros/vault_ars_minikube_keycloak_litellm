from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import patch

import httpx
import pytest

import agent_api
from errors import AppError
from identity import OboTokenService
from mcp_client import _find_authz_denied_response, _raise_if_authz_denied
from scoped_tool import make_scoped_tool


def _run(coro):
    return asyncio.run(coro)


class TemplateTool:
    """Stand-in for a langchain-mcp-adapters StructuredTool."""

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description or f"Template for {name}"
        self.args_schema = None


class TokenServiceStub:
    def __init__(self, raise_on_resolve: AppError | None = None):
        self.calls: list[dict] = []
        self._raise = raise_on_resolve

    def resolve_token(self, *, subject_token, request_id, scopes):
        self.calls.append(
            {
                "subject_token": subject_token,
                "request_id": request_id,
                "scopes": list(scopes),
            }
        )
        if self._raise is not None:
            raise self._raise
        return f"obo:{subject_token}:{','.join(sorted(scopes))}"


@pytest.fixture
def template():
    return TemplateTool("list_all_users")


def test_per_call_obo_uses_required_scopes(template):
    service = TokenServiceStub()
    captured: dict = {}

    async def fake_invoke(*, user_mcp_url, tool_name, args, obo_token, request_id, timeout_seconds=30.0, **_kwargs):
        captured["url"] = user_mcp_url
        captured["tool_name"] = tool_name
        captured["args"] = args
        captured["obo_token"] = obo_token
        captured["request_id"] = request_id
        captured["timeout_seconds"] = timeout_seconds
        return [{"email": "a@b.com"}]

    with patch("scoped_tool.invoke_mcp_tool", fake_invoke):
        wrapped = make_scoped_tool(
            template_tool=template,
            required_scopes=["users.read"],
            token_service=service,
            subject_token="user-jwt",
            request_id="req-1",
            user_mcp_url="http://user-mcp.local/mcp",
        )
        result = _run(wrapped.ainvoke({}))

    assert result == [{"email": "a@b.com"}]
    assert service.calls == [
        {
            "subject_token": "user-jwt",
            "request_id": "req-1",
            "scopes": ["users.read"],
        }
    ]
    assert captured["obo_token"] == "obo:user-jwt:users.read"
    assert captured["tool_name"] == "list_all_users"
    assert captured["request_id"] == "req-1"


def test_assurance_from_tool_meta_is_recorded(template):
    from assurance import AssuranceTracker

    service = TokenServiceStub()
    tracker = AssuranceTracker()
    assurance = {"tool": "list_all_users", "decision": "ALLOW", "current_loa": 1, "required_loa": 1}

    async def fake_invoke(**kwargs):
        return [{"email": "a@b.com"}]

    with patch("scoped_tool.invoke_mcp_tool", fake_invoke), patch(
        "scoped_tool.get_last_tool_meta", lambda: {"assurance": assurance}
    ):
        wrapped = make_scoped_tool(
            template_tool=template,
            required_scopes=["users.read"],
            token_service=service,
            subject_token="user-jwt",
            request_id="req-1",
            user_mcp_url="http://user-mcp.local/mcp",
            assurance_tracker=tracker,
        )
        _run(wrapped.ainvoke({}))

    assert tracker.get_last("user-jwt") == assurance


def test_no_assurance_meta_leaves_tracker_untouched(template):
    from assurance import AssuranceTracker

    service = TokenServiceStub()
    tracker = AssuranceTracker()

    async def fake_invoke(**kwargs):
        return [{"email": "a@b.com"}]

    with patch("scoped_tool.invoke_mcp_tool", fake_invoke), patch(
        "scoped_tool.get_last_tool_meta", lambda: None
    ):
        wrapped = make_scoped_tool(
            template_tool=template,
            required_scopes=["users.read"],
            token_service=service,
            subject_token="user-jwt",
            request_id="req-1",
            user_mcp_url="http://user-mcp.local/mcp",
            assurance_tracker=tracker,
        )
        _run(wrapped.ainvoke({}))

    assert tracker.get_last("user-jwt") is None


def test_token_exchange_failure_returns_permission_denied_string():
    service = TokenServiceStub(
        raise_on_resolve=AppError(
            status_code=403,
            error="forbidden",
            message="user lacks users.write",
        )
    )

    async def should_not_be_called(**kwargs):
        raise AssertionError("invoke_mcp_tool should not run when exchange fails")

    with patch("scoped_tool.invoke_mcp_tool", should_not_be_called):
        wrapped = make_scoped_tool(
            template_tool=TemplateTool("create_user"),
            required_scopes=["users.write"],
            token_service=service,
            subject_token="user-jwt",
            request_id="req-2",
            user_mcp_url="http://user-mcp.local/mcp",
        )
        result = _run(wrapped.ainvoke({}))

    assert isinstance(result, str)
    assert "Permission denied" in result
    assert "users.write" in result
    assert "create_user" in result


def test_mcp_insufficient_scope_returns_permission_denied_string():
    service = TokenServiceStub()

    async def fake_invoke(**kwargs):
        raise RuntimeError(
            "Tool 'create_user' requires scope(s) ['users.write'] but the OBO "
            "token grants ['users.read']. (insufficient_scope)"
        )

    with patch("scoped_tool.invoke_mcp_tool", fake_invoke):
        wrapped = make_scoped_tool(
            template_tool=TemplateTool("create_user"),
            required_scopes=["users.write"],
            token_service=service,
            subject_token="user-jwt",
            request_id="req-3",
            user_mcp_url="http://user-mcp.local/mcp",
        )
        result = _run(wrapped.ainvoke({}))

    assert isinstance(result, str)
    assert "Permission denied" in result
    assert "users.write" in result


def test_other_mcp_errors_become_tool_message():
    """Any tool failure that isn't insufficient_scope (approval denied,
    upstream timeout, downstream service error, ...) must become a
    ToolMessage the LLM can explain to the user, not an unhandled exception
    that 500s the whole request with no explanation reaching them."""
    service = TokenServiceStub()

    async def fake_invoke(**kwargs):
        raise RuntimeError("something else went wrong")

    with patch("scoped_tool.invoke_mcp_tool", fake_invoke):
        wrapped = make_scoped_tool(
            template_tool=TemplateTool("list_all_users"),
            required_scopes=["users.read"],
            token_service=service,
            subject_token="user-jwt",
            request_id="req-4",
            user_mcp_url="http://user-mcp.local/mcp",
        )
        result = _run(wrapped.ainvoke({}))

    assert isinstance(result, str)
    assert "list_all_users" in result
    assert "something else went wrong" in result


def _make_http_status_error(
    status_code: int, headers: dict[str, str] | None = None
) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://user-mcp.local/mcp")
    response = httpx.Response(status_code, headers=headers or {}, request=request)
    return httpx.HTTPStatusError(
        f"{status_code} response", request=request, response=response
    )


def test_find_authz_denied_response_detects_direct_403_with_reason():
    exc = _make_http_status_error(
        403, {"x-authz-reason": "agent=ai-agent tool=create_user not permitted"}
    )
    response = _find_authz_denied_response(exc)
    assert response is not None
    assert response.headers["x-authz-reason"] == (
        "agent=ai-agent tool=create_user not permitted"
    )


def test_find_authz_denied_response_unwraps_exception_group():
    inner = _make_http_status_error(403, {"x-authz-reason": "default-deny"})
    group = BaseExceptionGroup("mcp transport failure", [inner])
    response = _find_authz_denied_response(group)
    assert response is not None
    assert response.headers["x-authz-reason"] == "default-deny"


def test_find_authz_denied_response_walks_cause_chain():
    inner = _make_http_status_error(403, {"x-authz-reason": "denied-by-policy"})
    outer = RuntimeError("wrapped")
    outer.__cause__ = inner
    response = _find_authz_denied_response(outer)
    assert response is not None
    assert response.headers["x-authz-reason"] == "denied-by-policy"


def test_find_authz_denied_response_ignores_403_without_authz_reason_header():
    exc = _make_http_status_error(403, {})
    assert _find_authz_denied_response(exc) is None


def test_find_authz_denied_response_ignores_non_403():
    exc = _make_http_status_error(500, {"x-authz-reason": "should-be-ignored"})
    assert _find_authz_denied_response(exc) is None


def test_raise_if_authz_denied_raises_app_error_with_reason():
    exc = _make_http_status_error(
        403, {"x-authz-reason": "tool=delete_user_by_email not permitted"}
    )
    with pytest.raises(AppError) as excinfo:
        _raise_if_authz_denied(exc, request_id="req-x", tool_name="delete_user_by_email")

    assert excinfo.value.status_code == 403
    assert excinfo.value.error == "forbidden"
    assert excinfo.value.message == "tool=delete_user_by_email not permitted"


def test_raise_if_authz_denied_is_a_noop_when_not_authz_denied():
    exc = _make_http_status_error(500)
    _raise_if_authz_denied(exc, request_id="req-x", tool_name="list_all_users")


def test_scoped_tool_propagates_app_error_from_mcp_authz_denial():
    """A 403 surfaced from invoke_mcp_tool must NOT be converted into a
    permission-denied ToolMessage string — it must propagate so the FastAPI
    handler can return 403 to the web-app."""
    service = TokenServiceStub()

    async def fake_invoke(**kwargs):
        raise AppError(
            status_code=403,
            error="forbidden",
            message="agent=ai-agent tool=create_user not permitted",
        )

    with patch("scoped_tool.invoke_mcp_tool", fake_invoke):
        wrapped = make_scoped_tool(
            template_tool=TemplateTool("create_user"),
            required_scopes=["users.write"],
            token_service=service,
            subject_token="user-jwt",
            request_id="req-authz",
            user_mcp_url="http://user-mcp.local/mcp",
        )
        with pytest.raises(AppError) as excinfo:
            _run(wrapped.ainvoke({}))

    assert excinfo.value.status_code == 403
    assert excinfo.value.error == "forbidden"
    assert excinfo.value.message == "agent=ai-agent tool=create_user not permitted"


def test_invoke_mcp_tool_converts_ext_authz_403_to_app_error(monkeypatch):
    """End-to-end inside mcp_client: when opening the session raises an
    HTTPStatusError carrying 403 + x-authz-reason, invoke_mcp_tool turns it
    into AppError."""
    from mcp_client import invoke_mcp_tool

    class FakeSessionContext:
        async def __aenter__(self):
            raise _make_http_status_error(
                403, {"x-authz-reason": "method=tools/call tool=create_user not permitted"}
            )

        async def __aexit__(self, *exc_info):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def session(self, *args, **kwargs):
            return FakeSessionContext()

    monkeypatch.setattr(
        "mcp_client._import_multi_server_client", lambda: FakeClient
    )
    monkeypatch.setattr(
        "mcp_client._import_convert_call_tool_result", lambda: (lambda r: (r, None))
    )

    with pytest.raises(AppError) as excinfo:
        _run(
            invoke_mcp_tool(
                user_mcp_url="http://user-mcp.local/mcp",
                tool_name="create_user",
                args={},
                obo_token="obo-token",
                request_id="req-1",
            )
        )

    assert excinfo.value.status_code == 403
    assert excinfo.value.error == "forbidden"
    assert excinfo.value.message == (
        "method=tools/call tool=create_user not permitted"
    )


def test_extract_required_scopes_reads_meta():
    from mcp_client import extract_required_scopes

    class FakeTool:
        metadata = {"_meta": {"required_scopes": ["users.read", "users.write"]}}

    assert extract_required_scopes(FakeTool()) == ["users.read", "users.write"]


def test_extract_required_scopes_handles_missing_meta():
    from mcp_client import extract_required_scopes

    class A:
        metadata = None

    class B:
        metadata = {}

    class C:
        metadata = {"_meta": None}

    class D:
        metadata = {"_meta": {"other": "value"}}

    assert extract_required_scopes(A()) == []
    assert extract_required_scopes(B()) == []
    assert extract_required_scopes(C()) == []
    assert extract_required_scopes(D()) == []


def test_extract_required_scopes_falls_back_for_litellm_prefixed_names():
    from mcp_client import canonical_mcp_tool_name, extract_required_scopes

    assert canonical_mcp_tool_name("user_mcp-list_all_users") == "list_all_users"
    assert canonical_mcp_tool_name("list_all_users") == "list_all_users"

    class Prefixed:
        name = "user_mcp-create_user"
        metadata = {}

    class Unprefixed:
        name = "list_all_users"
        metadata = None

    assert extract_required_scopes(Prefixed()) == ["users.write"]
    assert extract_required_scopes(Unprefixed()) == ["users.read"]


def test_mcp_request_headers_carry_no_litellm_credential():
    from mcp_client import mcp_request_headers

    discovery = mcp_request_headers("req-1")
    assert discovery == {"X-Request-ID": "req-1"}

    headers = mcp_request_headers("req-3", obo_token="obo-jwt")
    assert headers == {
        "X-Request-ID": "req-3",
        "Authorization": "Bearer obo-jwt",
    }


def _build_settings(tmp_path):
    actor_token_path = tmp_path / "actor"
    actor_token_path.write_text("actor-token", encoding="utf-8")
    return type(agent_api.SETTINGS)(
        model="x",
        ollama_base_url=None,
        litellm_base_url=None,
        actor_token_path=actor_token_path,
        token_exchange_url="http://t.local/obo",
        token_exchange_timeout_seconds=1.0,
        obo_role_name="r",
        bypass_auth_token_exchange=False,
        host="0",
        port=0,
        log_level="INFO",
        user_mcp_url="http://u.local/mcp",
        mcp_tool_call_timeout_seconds=30.0,
    )


def test_resolve_token_caches_per_scope_set(tmp_path):
    service = OboTokenService(
        settings=_build_settings(tmp_path), logger=logging.getLogger("test")
    )
    calls: list[str] = []

    def fake_exchange(subject_token, actor_token, request_id, scope):
        calls.append(scope)
        import time

        return f"obo-{scope}", time.time() + 600

    service.perform_token_exchange = fake_exchange  # type: ignore[assignment]

    a = service.resolve_token(subject_token="u", request_id="r", scopes=["users.read"])
    b = service.resolve_token(subject_token="u", request_id="r", scopes=["users.read"])
    c = service.resolve_token(subject_token="u", request_id="r", scopes=["users.write"])

    assert a == b == "obo-users.read"
    assert c == "obo-users.write"
    assert calls == ["users.read", "users.write"]


def test_resolve_token_normalizes_scope_order(tmp_path):
    service = OboTokenService(
        settings=_build_settings(tmp_path), logger=logging.getLogger("test")
    )
    calls: list[str] = []

    def fake_exchange(subject_token, actor_token, request_id, scope):
        calls.append(scope)
        import time

        return f"obo-{scope}", time.time() + 600

    service.perform_token_exchange = fake_exchange  # type: ignore[assignment]

    first = service.resolve_token(
        subject_token="u", request_id="r", scopes=["users.write", "users.read"]
    )
    second = service.resolve_token(
        subject_token="u", request_id="r", scopes=["users.read", "users.write"]
    )

    assert first == second
    # Single exchange because both calls normalize to the same scope key.
    assert calls == ["users.read users.write"]
