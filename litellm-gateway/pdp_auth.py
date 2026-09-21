"""LiteLLM custom_auth: the gateway's PDP admission check.

Public health/UI/SSO routes are admitted without credentials. Service
callers are identified by the mesh, not a shared secret: the inbound Envoy
(builtin/lua extension on litellm-gateway's ServiceDefaults, see
infra/local-minikube/mesh-timeouts.yaml) copies the verified mTLS peer's
SPIFFE ID into x-mesh-caller-spiffe, and only ADMITTED_SERVICES are admitted.
Everything else — admin-UI SSO JWTs, virtual keys, anything arriving through
litellm-api-gateway — falls through to LiteLLM's default auth.

Chat hops (/v1/agent/*) also verify the Keycloak subject JWT (JWKS, exp,
aud, iss) before the request is forwarded to ai-agent. tools/call stays
in pdp_mcp.py.

The header is only trustworthy while that extension is applied: it is what
strips any client-supplied copy and sets the real one.

Structured PDP_Decision logs use the Telefonica catalog field names.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import jwt

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


def is_chat_path(path: str) -> bool:
    """Subject JWT is required on the chat pass-through, not on LLM/MCP hops."""
    return path == "/v1/agent" or path.startswith("/v1/agent/")


class SubjectJwtError(Exception):
    def __init__(self, message: str, error: str):
        super().__init__(message)
        self.message = message
        self.error = error


class SubjectJwtValidator:
    """Verify the Keycloak access token the web BFF forwards on each chat turn."""

    def __init__(
        self,
        jwks_url: str,
        issuer: str,
        audience: str,
        jwks_cache_seconds: int = 3600,
    ):
        self._jwks_client = jwt.PyJWKClient(
            jwks_url,
            cache_keys=True,
            lifespan=jwks_cache_seconds,
        )
        self._issuer = issuer
        self._audience = audience

    def validate(self, token: str) -> dict[str, Any]:
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token).key
        except jwt.PyJWKClientError as exc:
            raise SubjectJwtError(f"Unable to fetch signing key: {exc}", "invalid_token") from exc
        except jwt.DecodeError as exc:
            raise SubjectJwtError(f"Malformed token: {exc}", "invalid_token") from exc
        try:
            return jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iat", "aud", "iss"]},
                leeway=30,
            )
        except jwt.ExpiredSignatureError as exc:
            raise SubjectJwtError("Bearer token has expired.", "expired_token") from exc
        except jwt.InvalidAudienceError as exc:
            raise SubjectJwtError(
                "Token audience does not match the chat client.",
                "invalid_audience",
            ) from exc
        except jwt.InvalidIssuerError as exc:
            raise SubjectJwtError("Token issuer is not trusted.", "invalid_issuer") from exc
        except jwt.InvalidTokenError as exc:
            raise SubjectJwtError(f"Token is invalid: {exc}", "invalid_token") from exc


_SUBJECT_VALIDATOR: SubjectJwtValidator | None = None


def subject_validator() -> SubjectJwtValidator:
    global _SUBJECT_VALIDATOR
    if _SUBJECT_VALIDATOR is None:
        jwks_url = os.getenv("PEP_KEYCLOAK_JWKS_URL", "")
        issuer = os.getenv("PEP_SUBJECT_ISSUER") or os.getenv("PEP_MCP_ISSUER", "")
        audience = os.getenv("PEP_SUBJECT_AUDIENCE", "token-exchange")
        if not jwks_url or not issuer or not audience:
            raise SubjectJwtError(
                "PEP_KEYCLOAK_JWKS_URL, PEP_SUBJECT_ISSUER (or PEP_MCP_ISSUER), "
                "and PEP_SUBJECT_AUDIENCE are required to verify chat tokens.",
                "configuration_error",
            )
        _SUBJECT_VALIDATOR = SubjectJwtValidator(jwks_url, issuer, audience)
    return _SUBJECT_VALIDATOR


def reset_subject_validator() -> None:
    """Drop the cached JWKS client. Tests call this after changing env."""
    global _SUBJECT_VALIDATOR
    _SUBJECT_VALIDATOR = None


def strip_bearer(value: str | None) -> str:
    raw = (value or "").strip()
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return raw


def decide(*, path: str, caller: str | None) -> str:
    if is_public_path(path):
        return "ALLOW"
    if caller in ADMITTED_SERVICES:
        return "ALLOW"
    return "DENY"


def _emit_audit(payload: dict[str, Any]) -> None:
    """Write one JSON line to stdout.

    LiteLLM's proxy logging config swallows loggers named litellm-gateway.*,
    so kubectl logs never see LOGGER.info. print() always reaches the pod
    stream the hop viewer tails.
    """
    line = json.dumps(payload, ensure_ascii=True, default=str, separators=(",", ":"))
    print(line, flush=True)
    LOGGER.info("%s", line)


def _should_audit(path: str, caller: str | None) -> bool:
    if is_public_path(path) or "/health" in path:
        return False
    return bool(caller) or path.startswith("/v1/") or path.startswith("/user_mcp")


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
    request_id = header_value(request.headers, "x-request-id")
    if _should_audit(path, caller):
        public = is_public_path(path)
        _emit_audit(
            {
                "event": "pdp_decision",
                "PDP_Decision": decision,
                "path": path,
                "caller": caller,
                "request_id": request_id or "-",
                "pep": "litellm-gateway/pdp_auth.user_api_key_auth",
                "pdp": "litellm-gateway/pdp_auth.decide",
                "pdp_package": "pdp_auth",
                "pdp_policy": (
                    "SPIFFE x-mesh-caller-spiffe in ADMITTED_SERVICES="
                    + ",".join(sorted(ADMITTED_SERVICES))
                    + "; otherwise fall through to LiteLLM SSO/virtual-key"
                ),
                "enforce": (
                    "admit_mesh"
                    if decision == "ALLOW" and caller in ADMITTED_SERVICES
                    else "admit_public"
                    if decision == "ALLOW" and public
                    else "fallthrough_litellm_auth"
                ),
                "reason": (
                    "admitted-service"
                    if caller in ADMITTED_SERVICES
                    else "public-path"
                    if public
                    else "not-in-admitted-services"
                ),
            }
        )
    if decision != "ALLOW":
        # SSO JWTs, virtual keys and the master key are LiteLLM's own auth.
        return await _default_user_api_key_auth(request, api_key)

    if is_chat_path(path) and caller == "default/web":
        token = strip_bearer(api_key) or strip_bearer(
            header_value(request.headers, "authorization")
        )
        try:
            claims = subject_validator().validate(token)
        except SubjectJwtError as exc:
            _emit_audit(
                {
                    "event": "token_chain_subject",
                    "PDP_Decision": "DENY",
                    "path": path,
                    "caller": caller,
                    "request_id": request_id or "-",
                    "pep": "litellm-gateway/pdp_auth.subject_jwt",
                    "pdp": "keycloak-jwks",
                    "pdp_package": "subject",
                    "pdp_policy": (
                        "RS256 JWKS PEP_KEYCLOAK_JWKS_URL; aud=PEP_SUBJECT_AUDIENCE; "
                        "iss=PEP_SUBJECT_ISSUER; require exp,iat,aud,iss"
                    ),
                    "enforce": "deny_subject_jwt",
                    "reason": exc.message,
                    "error": exc.error,
                    "token": "subject",
                }
            )
            from litellm.proxy._types import ProxyException

            raise ProxyException(
                message=exc.message,
                type=exc.error,
                param=None,
                code=401,
            ) from exc
        _emit_audit(
            {
                "event": "token_chain_subject",
                "PDP_Decision": "ALLOW",
                "path": path,
                "caller": caller,
                "request_id": request_id or "-",
                "preferred_username": claims.get("preferred_username") or "-",
                "pep": "litellm-gateway/pdp_auth.subject_jwt",
                "pdp": "keycloak-jwks",
                "pdp_package": "subject",
                "pdp_policy": (
                    "RS256 JWKS PEP_KEYCLOAK_JWKS_URL; aud=PEP_SUBJECT_AUDIENCE; "
                    "iss=PEP_SUBJECT_ISSUER; require exp,iat,aud,iss"
                ),
                "enforce": "verify_subject_jwt",
                "reason": "signature-exp-aud-iss",
                "token": "subject",
                "aud": claims.get("aud"),
                "iss": claims.get("iss"),
            }
        )

    return _allowed_auth(caller, public=is_public_path(path))
