from types import SimpleNamespace

import asyncio

from pdp_mcp import (
    ALLOW,
    McpPep,
    McpPepGuardrail,
    PepDenied,
    extra_headers_for,
    strip_bearer,
)


def test_strip_bearer():
    assert strip_bearer("Bearer abc") == "abc"
    assert strip_bearer("bearer abc") == "abc"
    assert strip_bearer(None) is None


class _Validator:
    def __init__(self, claims):
        self.claims = claims

    def validate(self, token):
        assert token == "obo-jwt"
        return self.claims


class _Opa:
    def __init__(self, result: dict):
        self.result = result
        self.calls = []

    async def decide(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class _Ciba:
    async def fetch_access_token(self, login_hint, binding_message, scope):
        return "ciba-jwt"


def _pep(*, opa_result: dict, claims=None) -> McpPep:
    return McpPep(
        source="default/litellm-gateway",
        dest="default/user-mcp",
        jwt_validator=_Validator(
            claims
            or {
                "preferred_username": "writer",
                "scope": "users.read users.write",
            }
        ),
        opa=_Opa(opa_result),
        ciba=_Ciba(),
    )


def test_authorize_read_returns_obo_and_loa1():
    pep = _pep(opa_result={"allow": True, "ciba_required": False, "reason": "allow"})
    headers = asyncio.run(pep.authorize("list_all_users", "Bearer obo-jwt"))
    assert headers == extra_headers_for(
        jwt_token="obo-jwt",
        decision=ALLOW,
        current_loa=1,
        required_loa=1,
    )


def test_authorize_read_emits_pep_pdp_json(capsys):
    pep = _pep(opa_result={"allow": True, "ciba_required": False, "reason": "allow"})
    asyncio.run(pep.authorize("list_all_users", "Bearer obo-jwt", request_id="req-1"))
    line = capsys.readouterr().out.strip().splitlines()[-1]
    payload = __import__("json").loads(line)
    assert payload["event"] == "pdp_decision"
    assert payload["PDP_Decision"] == ALLOW
    assert payload["enforce"] == "inject_obo_jwt"
    assert payload["pep"] == "litellm-gateway/pdp_mcp.pre_mcp_call"
    assert payload["pdp"] == "opa-server"
    assert payload["pdp_package"] == "mcp.pep"
    assert payload["reason"] == "allow"
    assert payload["tool"] == "list_all_users"
    assert payload["required_scopes"] == ["users.read"]
    assert payload["granted_scopes"] == ["users.read", "users.write"]
    assert payload["ciba_tool"] is False
    assert payload["request_id"] == "req-1"


def test_authorize_write_ciba_returns_loa2():
    pep = _pep(opa_result={"allow": True, "ciba_required": True, "reason": "step-up"})
    headers = asyncio.run(pep.authorize("create_user", "obo-jwt"))
    assert headers["Authorization"] == "Bearer ciba-jwt"
    assert headers["X-PEP-LoA"] == "2"
    assert headers["X-PEP-Required-LoA"] == "2"


def test_authorize_denies_delete_before_opa_and_ciba():
    opa = _Opa({"allow": True, "ciba_required": True, "reason": "step-up"})
    pep = McpPep(
        source="default/litellm-gateway",
        dest="default/user-mcp",
        jwt_validator=_Validator({"preferred_username": "writer", "scope": "users.write"}),
        opa=opa,
        ciba=_Ciba(),
    )
    try:
        asyncio.run(pep.authorize("delete_user_by_email", "obo-jwt"))
        raise AssertionError("expected PepDenied")
    except PepDenied as exc:
        assert exc.error == "tool_disabled"
    assert opa.calls == []


def test_authorize_denies_unknown_tool():
    pep = _pep(opa_result={"allow": False, "ciba_required": False, "reason": "catalog"})
    try:
        asyncio.run(pep.authorize("excluir_conta", "obo-jwt"))
        raise AssertionError("expected PepDenied")
    except PepDenied as exc:
        assert "not allowed" in exc.message


def test_authorize_denies_missing_scope():
    pep = _pep(
        opa_result={"allow": False, "ciba_required": False, "reason": "insufficient_scope"},
        claims={"preferred_username": "reader", "scope": "users.read"},
    )
    try:
        asyncio.run(pep.authorize("create_user", "obo-jwt"))
        raise AssertionError("expected PepDenied")
    except PepDenied as exc:
        assert exc.error == "insufficient_scope"


def test_authorize_denies_missing_bearer():
    pep = _pep(opa_result={"allow": True, "ciba_required": False, "reason": "allow"})
    try:
        asyncio.run(pep.authorize("list_all_users", None))
        raise AssertionError("expected PepDenied")
    except PepDenied as exc:
        assert "bearer" in exc.message.lower()


def test_guardrail_hook_returns_extra_headers():
    pep = _pep(opa_result={"allow": True, "ciba_required": False, "reason": "allow"})
    hook = McpPepGuardrail(pep=pep)
    result = asyncio.run(
        hook.async_pre_call_hook(
            user_api_key_dict=SimpleNamespace(),
            cache=None,
            data={"mcp_tool_name": "list_all_users", "incoming_bearer_token": "obo-jwt"},
            call_type="call_mcp_tool",
        )
    )
    assert result["extra_headers"]["Authorization"] == "Bearer obo-jwt"


def test_guardrail_hook_raises_on_deny():
    pep = _pep(opa_result={"allow": False, "ciba_required": False, "reason": "catalog"})
    hook = McpPepGuardrail(pep=pep)
    try:
        asyncio.run(
            hook.async_pre_call_hook(
                user_api_key_dict=None,
                cache=None,
                data={"name": "excluir_conta", "incoming_bearer_token": "obo-jwt"},
                call_type="call_mcp_tool",
            )
        )
        raise AssertionError("expected Exception")
    except Exception as exc:
        assert "not allowed" in str(exc)
