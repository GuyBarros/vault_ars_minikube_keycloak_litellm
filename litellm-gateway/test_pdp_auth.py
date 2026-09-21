from types import SimpleNamespace

from pdp_auth import (
    _ensure_bearer_prefix,
    decide,
    mesh_caller,
    user_api_key_auth,
)

AI_AGENT = "spiffe://11111111-2222.consul/ap/default/ns/default/dc/dc1/svc/ai-agent"


def test_mesh_caller_extracts_namespace_and_service():
    assert mesh_caller(AI_AGENT) == "default/ai-agent"
    # Community edition has no /ap/<partition> segment.
    assert mesh_caller("spiffe://td.consul/ns/default/dc/dc1/svc/web") == "default/web"
    assert mesh_caller("spiffe://td.consul/ns/opa/dc/dc1/svc/opa-service") == "opa/opa-service"


def test_mesh_caller_rejects_malformed_ids():
    assert mesh_caller(None) is None
    assert mesh_caller("") is None
    assert mesh_caller("ai-agent") is None
    assert mesh_caller("spiffe://td.consul/ns/default/dc/dc1/svc/ai-agent/extra") is None


def test_decide_allows_health_without_caller():
    assert decide(path="/health", caller=None) == "ALLOW"
    assert decide(path="/health/readiness", caller=None) == "ALLOW"


def test_decide_allows_admin_ui_without_caller():
    assert decide(path="/ui", caller=None) == "ALLOW"
    assert decide(path="/ui/", caller=None) == "ALLOW"
    assert decide(path="/login", caller=None) == "ALLOW"
    assert decide(path="/sso/callback", caller=None) == "ALLOW"
    assert decide(path="/fallback/login", caller=None) == "ALLOW"
    assert decide(path="/.well-known/litellm-ui-config", caller=None) == "ALLOW"
    assert decide(path="/litellm-asset-prefix/_next/static/chunks/x.css", caller=None) == "ALLOW"


def test_decide_admits_only_listed_mesh_services_on_protected_paths():
    assert decide(path="/v1/agent/query", caller="default/web") == "ALLOW"
    assert decide(path="/v1/chat/completions", caller="default/ai-agent") == "ALLOW"
    assert decide(path="/user_mcp/mcp", caller="default/ai-agent") == "ALLOW"
    # The browser-facing gateway must never be admitted as a service caller.
    assert decide(path="/v1/chat/completions", caller="default/litellm-api-gateway") == "DENY"
    # Same service name in another Consul namespace is a different identity.
    assert decide(path="/v1/chat/completions", caller="other/ai-agent") == "DENY"
    assert decide(path="/v1/agent/query", caller=None) == "DENY"


def test_ensure_bearer_prefix():
    assert _ensure_bearer_prefix("eyJabc") == "Bearer eyJabc"
    assert _ensure_bearer_prefix("Bearer eyJabc") == "Bearer eyJabc"
    assert _ensure_bearer_prefix("bearer eyJabc") == "bearer eyJabc"
    assert _ensure_bearer_prefix("") == ""


def test_user_api_key_auth_delegates_sso_jwt(monkeypatch):
    async def fake_default(request, api_key):
        return {"delegated": api_key}

    monkeypatch.setattr("pdp_auth._default_user_api_key_auth", fake_default)
    request = SimpleNamespace(
        url=SimpleNamespace(path="/user/info"),
        headers={"authorization": "Bearer sso-jwt"},
    )
    import asyncio

    result = asyncio.run(user_api_key_auth(request, "sso-jwt"))
    assert result == {"delegated": "sso-jwt"}


def _request(path, headers):
    return SimpleNamespace(url=SimpleNamespace(path=path), headers=headers)


def test_audit_logs_the_full_spiffe_id_of_the_caller(monkeypatch, capsys):
    import asyncio

    async def fake_default(request, api_key):
        return None

    monkeypatch.setattr("pdp_auth._default_user_api_key_auth", fake_default)
    monkeypatch.setattr("pdp_auth._allowed_auth", lambda caller, public: None)
    asyncio.run(user_api_key_auth(_request("/v1/chat/completions", {"x-mesh-caller-spiffe": AI_AGENT}), "k"))
    import json

    audit = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert audit["caller"] == AI_AGENT  # not reduced to default/ai-agent
    assert audit["caller_service"] == "default/ai-agent"
    assert audit["PDP_Decision"] == "ALLOW"


def test_user_api_key_auth_admits_mesh_caller_as_admin(monkeypatch):
    import asyncio
    import sys
    from types import ModuleType

    class FakeAuth:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    fake_types = ModuleType("litellm.proxy._types")
    fake_types.UserAPIKeyAuth = FakeAuth
    fake_types.LitellmUserRoles = SimpleNamespace(PROXY_ADMIN="proxy_admin")
    for name in ("litellm", "litellm.proxy"):
        monkeypatch.setitem(sys.modules, name, sys.modules.get(name) or ModuleType(name))
    monkeypatch.setitem(sys.modules, "litellm.proxy._types", fake_types)

    async def fail_default(request, api_key):
        raise AssertionError("mesh caller must not fall through to default auth")

    monkeypatch.setattr("pdp_auth._default_user_api_key_auth", fail_default)
    result = asyncio.run(
        user_api_key_auth(
            _request("/v1/chat/completions", {"x-mesh-caller-spiffe": AI_AGENT}),
            "sk-litellm-local",
        )
    )
    assert result.user_role == "proxy_admin"
    assert result.api_key == "mesh:default/ai-agent"


def test_user_api_key_auth_ignores_unlisted_caller_and_master_key(monkeypatch):
    import asyncio

    async def fake_default(request, api_key):
        return {"delegated": api_key}

    monkeypatch.setattr("pdp_auth._default_user_api_key_auth", fake_default)
    gateway = "spiffe://td.consul/ap/default/ns/default/dc/dc1/svc/litellm-api-gateway"
    result = asyncio.run(
        user_api_key_auth(
            _request("/v1/chat/completions", {"x-mesh-caller-spiffe": gateway}),
            "sk-anything",
        )
    )
    assert result == {"delegated": "sk-anything"}


def test_default_auth_clears_custom_auth_during_call(monkeypatch):
    import sys
    from types import ModuleType

    fake_proxy = SimpleNamespace(user_custom_auth="custom")
    seen = {}

    async def fake_default(request, api_key):
        seen["during"] = fake_proxy.user_custom_auth
        seen["api_key"] = api_key
        return {"ok": True}

    for name in ("litellm", "litellm.proxy", "litellm.proxy.auth"):
        monkeypatch.setitem(sys.modules, name, sys.modules.get(name) or ModuleType(name))
    monkeypatch.setitem(sys.modules, "litellm.proxy.proxy_server", fake_proxy)
    monkeypatch.setitem(
        sys.modules,
        "litellm.proxy.auth.user_api_key_auth",
        SimpleNamespace(user_api_key_auth=fake_default),
    )
    import asyncio
    from pdp_auth import _default_user_api_key_auth

    result = asyncio.run(_default_user_api_key_auth(SimpleNamespace(), "jwt"))
    assert result == {"ok": True}
    assert seen["during"] is None
    assert seen["api_key"] == "Bearer jwt"
    assert fake_proxy.user_custom_auth == "custom"
