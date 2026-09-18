from types import SimpleNamespace

from pdp_auth import (
    _ensure_bearer_prefix,
    decide,
    presented_master_key,
    strip_bearer,
    user_api_key_auth,
)


def test_strip_bearer_accepts_with_and_without_prefix():
    assert strip_bearer("Bearer sk-abc") == "sk-abc"
    assert strip_bearer("sk-abc") == "sk-abc"
    assert strip_bearer("  bearer   sk-abc  ") == "sk-abc"
    assert strip_bearer("") is None
    assert strip_bearer(None) is None


def test_presented_master_key_prefers_x_litellm_header():
    assert (
        presented_master_key("Bearer eyJ-jwt", "Bearer sk-master") == "sk-master"
    )
    assert presented_master_key("Bearer sk-master", None) == "sk-master"
    assert presented_master_key(None, None) is None


def test_decide_allows_health_without_key():
    assert decide(path="/health", presented=None, master_key="sk-x") == "ALLOW"
    assert decide(path="/health/readiness", presented=None, master_key="sk-x") == "ALLOW"


def test_decide_allows_admin_ui_without_key():
    assert decide(path="/ui", presented=None, master_key="sk-x") == "ALLOW"
    assert decide(path="/ui/", presented=None, master_key="sk-x") == "ALLOW"
    assert decide(path="/login", presented=None, master_key="sk-x") == "ALLOW"
    assert decide(path="/sso/callback", presented=None, master_key="sk-x") == "ALLOW"
    assert decide(path="/fallback/login", presented=None, master_key="sk-x") == "ALLOW"
    assert decide(path="/.well-known/litellm-ui-config", presented=None, master_key="sk-x") == "ALLOW"
    assert decide(path="/litellm-asset-prefix/_next/static/chunks/x.css", presented=None, master_key="sk-x") == "ALLOW"


def test_decide_requires_matching_master_key_on_protected_paths():
    assert decide(path="/v1/agent/query", presented="sk-x", master_key="sk-x") == "ALLOW"
    assert decide(path="/user_mcp/mcp", presented="sk-x", master_key="sk-x") == "ALLOW"
    assert decide(path="/v1/chat/completions", presented="sk-wrong", master_key="sk-x") == "DENY"
    assert decide(path="/v1/agent/query", presented=None, master_key="sk-x") == "DENY"
    assert decide(path="/user_mcp/mcp", presented="sk-x", master_key=None) == "DENY"


def test_ensure_bearer_prefix():
    assert _ensure_bearer_prefix("eyJabc") == "Bearer eyJabc"
    assert _ensure_bearer_prefix("Bearer eyJabc") == "Bearer eyJabc"
    assert _ensure_bearer_prefix("bearer eyJabc") == "bearer eyJabc"
    assert _ensure_bearer_prefix("") == ""


def test_user_api_key_auth_delegates_sso_jwt(monkeypatch):
    async def fake_default(request, api_key):
        return {"delegated": api_key}

    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-x")
    monkeypatch.setattr("pdp_auth._default_user_api_key_auth", fake_default)
    request = SimpleNamespace(
        url=SimpleNamespace(path="/user/info"),
        headers={"authorization": "Bearer sso-jwt"},
    )
    import asyncio

    result = asyncio.run(user_api_key_auth(request, "sso-jwt"))
    assert result == {"delegated": "sso-jwt"}


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
