"""LiteLLM custom_auth: the gateway's PDP admission check.

Public health/UI/SSO routes are admitted without a key. Service callers
must present the master key as x-litellm-api-key (so Authorization can
stay the upstream JWT). Everything else — admin-UI SSO JWTs, virtual
keys — falls through to LiteLLM's default auth.

Structured PDP_Decision logs use the Telefonica catalog field names.
"""

from __future__ import annotations

import logging
import os
from typing import Any

LOGGER = logging.getLogger("litellm-gateway.pdp")

PUBLIC_PATH_PREFIXES = (
    "/health",
    "/metrics",
    "/favicon.ico",
    "/ui",
    "/login",
    "/logout",
    "/sso",
    "/fallback",
    "/.well-known",
    "/litellm-asset-prefix",
)

LITELLM_API_KEY_HEADER = "x-litellm-api-key"


def strip_bearer(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    prefix = "bearer "
    if stripped.lower().startswith(prefix):
        stripped = stripped[len(prefix) :].strip()
    return stripped or None


def presented_master_key(
    api_key: str | None,
    litellm_api_key_header: str | None,
) -> str | None:
    """Prefer x-litellm-api-key so Authorization can stay the upstream JWT."""
    return strip_bearer(litellm_api_key_header) or strip_bearer(api_key)


def is_public_path(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in PUBLIC_PATH_PREFIXES)


def decide(
    *,
    path: str,
    presented: str | None,
    master_key: str | None,
) -> str:
    if is_public_path(path):
        return "ALLOW"
    if not master_key:
        return "DENY"
    if presented and presented == master_key:
        return "ALLOW"
    return "DENY"


def header_value(headers: Any, name: str) -> str | None:
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    return getter(name) or getter(name.lower())


def _ensure_bearer_prefix(api_key: str) -> str:
    """LiteLLM's default auth discards tokens that lack a Bearer/Basic prefix."""
    stripped = api_key.strip()
    if not stripped:
        return stripped
    lower = stripped.lower()
    if lower.startswith("bearer ") or lower.startswith("basic "):
        return stripped
    return f"Bearer {stripped}"


async def _default_user_api_key_auth(request: Any, api_key: str) -> Any:
    """Run LiteLLM JWT / virtual-key auth without re-entering custom_auth.

    LiteLLM's user_api_key_auth always calls custom_auth first. Calling it
    from here without clearing ``user_custom_auth`` recurses until UI
    GETs (Agents, MCP Servers) 401 and the lists render empty.
    """
    import litellm.proxy.proxy_server as proxy_server
    from litellm.proxy.auth.user_api_key_auth import user_api_key_auth as default_auth

    saved = proxy_server.user_custom_auth
    proxy_server.user_custom_auth = None
    try:
        return await default_auth(request, _ensure_bearer_prefix(api_key))
    finally:
        proxy_server.user_custom_auth = saved


def _allowed_auth(presented: str | None, master_key: str | None, *, public: bool) -> Any:
    from litellm.proxy._types import LitellmUserRoles, UserAPIKeyAuth

    token = presented or master_key or "health"
    if public:
        return UserAPIKeyAuth(api_key=token)
    return UserAPIKeyAuth(
        api_key=token,
        user_id="litellm-gateway",
        user_role=LitellmUserRoles.PROXY_ADMIN,
    )


async def user_api_key_auth(request: Any, api_key: str) -> Any:
    path = request.url.path
    master_key = os.environ.get("LITELLM_MASTER_KEY")
    presented = presented_master_key(
        api_key, header_value(request.headers, LITELLM_API_KEY_HEADER)
    )
    decision = decide(path=path, presented=presented, master_key=master_key)
    LOGGER.info(
        "event=pdp_decision PDP_Decision=%s path=%s",
        decision,
        path,
    )
    if decision == "ALLOW":
        return _allowed_auth(presented, master_key, public=is_public_path(path))
    # SSO JWTs and virtual keys are LiteLLM's own auth, not the master key.
    return await _default_user_api_key_auth(request, api_key)
