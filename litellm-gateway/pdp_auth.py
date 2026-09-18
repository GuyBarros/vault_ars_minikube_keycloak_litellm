"""LiteLLM custom_auth: the gateway's PDP admission check.

Public health/UI/SSO routes are admitted without credentials. Service
callers are identified by the mesh, not a shared secret: the inbound Envoy
(builtin/lua extension on litellm-gateway's ServiceDefaults, see
infra/local-minikube/mesh-timeouts.yaml) copies the verified mTLS peer's
SPIFFE ID into x-mesh-caller-spiffe, and only ADMITTED_SERVICES are admitted.
Everything else — admin-UI SSO JWTs, virtual keys, anything arriving through
litellm-api-gateway — falls through to LiteLLM's default auth.

The header is only trustworthy while that extension is applied: it is what
strips any client-supplied copy and sets the real one.

Structured PDP_Decision logs use the Telefonica catalog field names.
"""

from __future__ import annotations

import logging
import re
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

CALLER_HEADER = "x-mesh-caller-spiffe"

# "<consul namespace>/<service>" of the workloads allowed to call as the gateway.
ADMITTED_SERVICES = frozenset({"default/ai-agent", "default/web"})

# Consul SPIFFE IDs end .../ns/<namespace>/dc/<datacenter>/svc/<service>
# (Enterprise adds a leading /ap/<partition>).
_SPIFFE_SERVICE = re.compile(r"/ns/(?P<ns>[^/]+)/dc/[^/]+/svc/(?P<svc>[^/]+)$")


def mesh_caller(spiffe_id: str | None) -> str | None:
    """Return "<namespace>/<service>" for a Consul SPIFFE ID, or None if malformed."""
    match = _SPIFFE_SERVICE.search((spiffe_id or "").strip())
    return f"{match['ns']}/{match['svc']}" if match else None


def is_public_path(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in PUBLIC_PATH_PREFIXES)


def decide(*, path: str, caller: str | None) -> str:
    if is_public_path(path):
        return "ALLOW"
    if caller in ADMITTED_SERVICES:
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


def _allowed_auth(caller: str | None, *, public: bool) -> Any:
    from litellm.proxy._types import LitellmUserRoles, UserAPIKeyAuth

    if public:
        return UserAPIKeyAuth(api_key="health")
    return UserAPIKeyAuth(
        api_key=f"mesh:{caller}",
        user_id="litellm-gateway",
        user_role=LitellmUserRoles.PROXY_ADMIN,
    )


async def user_api_key_auth(request: Any, api_key: str) -> Any:
    path = request.url.path
    caller = mesh_caller(header_value(request.headers, CALLER_HEADER))
    decision = decide(path=path, caller=caller)
    LOGGER.info(
        "event=pdp_decision PDP_Decision=%s path=%s caller=%s",
        decision,
        path,
        caller,
    )
    if decision == "ALLOW":
        return _allowed_auth(caller, public=is_public_path(path))
    # SSO JWTs, virtual keys and the master key are LiteLLM's own auth.
    return await _default_user_api_key_auth(request, api_key)
