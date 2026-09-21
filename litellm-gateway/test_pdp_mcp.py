from types import SimpleNamespace

import asyncio
import time

from pdp_mcp import (
    ALLOW,
    LOA2_MAX_AGE_SECONDS,
    McpPep,
    McpPepGuardrail,
    PepDenied,
    effective_loa,
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
    )


def test_authorize_read_returns_obo_and_loa1():
    pep = _pep(opa_result={"allow": True, "reason": "allow"})
    headers = asyncio.run(pep.authorize("list_all_users", "Bearer obo-jwt"))
    assert headers == extra_headers_for(
        jwt_token="obo-jwt",
        decision=ALLOW,
        current_loa=1,
        required_loa=1,
    )


def test_authorize_read_emits_pep_pdp_json(capsys):
    pep = _pep(opa_result={"allow": True, "reason": "allow"})
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
    assert payload["loa2_tool"] is False
    assert payload["request_id"] == "req-1"


def test_authorize_reads_loa_from_the_users_acr_claim():
    pep = _pep(
        opa_result={"allow": True, "required_loa": 2, "reason": "loa2"},
        claims={"preferred_username": "writer", "scope": "users.write", "acr": "2", "acr_time": int(time.time())},
    )
    headers = asyncio.run(pep.authorize("create_user", "Bearer obo-jwt"))
    assert headers["Authorization"] == "Bearer obo-jwt"
    assert headers["X-PEP-LoA"] == "2"
    assert headers["X-PEP-Required-LoA"] == "2"
    assert pep._opa.calls[0]["loa"] == 2


def test_step_up_expires_after_the_window():
    now = 1_000_000
    assert effective_loa({"acr": "2", "acr_time": now - 10}, now) == 2
    assert effective_loa({"acr": "2", "acr_time": now - LOA2_MAX_AGE_SECONDS}, now) == 2
    assert effective_loa({"acr": "2", "acr_time": now - LOA2_MAX_AGE_SECONDS - 1}, now) == 1
    # a token that is hours old counts as baseline even though it is still valid
    assert effective_loa({"acr": "2", "iat": now - 3 * 3600}, now) == 1
    # the step-up time wins over the (fresh) iat an exchange gives the OBO token
    assert effective_loa({"acr": "2", "acr_time": now - 3600, "iat": now}, now) == 1
    # unknown age: not trusted as elevated
    assert effective_loa({"acr": "2"}, now) == 1
    assert effective_loa({"acr": "1", "iat": now}, now) == 1
    assert effective_loa({}, now) == 1


def test_authorize_treats_an_expired_step_up_as_loa1(capsys):
    stale = int(time.time()) - LOA2_MAX_AGE_SECONDS - 60
    pep = _pep(
        opa_result={"allow": False, "step_up_required": True, "required_loa": 2, "reason": "step_up_required"},
        claims={"preferred_username": "writer", "scope": "users.write", "acr": "2", "acr_time": stale},
    )
    try:
        asyncio.run(pep.authorize("create_user", "Bearer obo-jwt"))
    except PepDenied as exc:
        assert exc.error == "step_up_required"
    assert pep._opa.calls[0]["loa"] == 1
    payload = __import__("json").loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["loa_expired"] is True
    assert payload["loa_age_seconds"] >= LOA2_MAX_AGE_SECONDS


def test_authorize_sends_loa1_to_opa_without_an_acr_claim():
    pep = _pep(opa_result={"allow": True, "reason": "allow"})
    asyncio.run(pep.authorize("list_all_users", "Bearer obo-jwt"))
    assert pep._opa.calls[0]["loa"] == 1


def test_authorize_requires_step_up_when_opa_says_so(capsys):
    pep = _pep(opa_result={"allow": False, "step_up_required": True, "required_loa": 2, "reason": "step_up_required"})
    try:
        asyncio.run(pep.authorize("create_user", "Bearer obo-jwt"))
    except PepDenied as exc:
        assert exc.error == "step_up_required"
        assert "acr_values=2" in exc.message
    else:
        raise AssertionError("expected PepDenied")
    payload = __import__("json").loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["PDP_Decision"] == "STEP_UP_REQUIRED"
    assert payload["LoA_Level"] == 1 and payload["Required_LoA"] == 2
    assert payload["enforce"] == "step_up_login"


def test_authorize_denies_unknown_tool():
    pep = _pep(opa_result={"allow": False, "reason": "catalog"})
    try:
        asyncio.run(pep.authorize("delete_user_by_email", "obo-jwt"))
        raise AssertionError("expected PepDenied")
    except PepDenied as exc:
        assert "not allowed" in exc.message


def test_authorize_denies_missing_scope():
    pep = _pep(
        opa_result={"allow": False, "reason": "insufficient_scope"},
        claims={"preferred_username": "reader", "scope": "users.read"},
    )
    try:
        asyncio.run(pep.authorize("create_user", "obo-jwt"))
        raise AssertionError("expected PepDenied")
    except PepDenied as exc:
        assert exc.error == "insufficient_scope"


def test_authorize_denies_missing_bearer():
    pep = _pep(opa_result={"allow": True, "reason": "allow"})
    try:
        asyncio.run(pep.authorize("list_all_users", None))
        raise AssertionError("expected PepDenied")
    except PepDenied as exc:
        assert "bearer" in exc.message.lower()


def test_guardrail_hook_returns_extra_headers():
    pep = _pep(opa_result={"allow": True, "reason": "allow"})
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
    pep = _pep(opa_result={"allow": False, "reason": "catalog"})
    hook = McpPepGuardrail(pep=pep)
    try:
        asyncio.run(
            hook.async_pre_call_hook(
                user_api_key_dict=None,
                cache=None,
                data={"name": "delete_user_by_email", "incoming_bearer_token": "obo-jwt"},
                call_type="call_mcp_tool",
            )
        )
        raise AssertionError("expected Exception")
    except Exception as exc:
        assert "not allowed" in str(exc)
