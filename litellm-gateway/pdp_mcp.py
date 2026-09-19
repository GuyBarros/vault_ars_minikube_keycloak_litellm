"""LiteLLM PEP for MCP tools/call.

opa-server is the PDP: catalog + tool→scope + CIBA switch
(POST /v1/data/mcp/pep/decision). This module intercepts the hop, then
returns extra_headers so LiteLLM forwards the (OBO or CIBA) JWT to
user-mcp runtime.

Must not define apply_guardrail — LiteLLM would then route through the
unified text guardrail and drop extra_headers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

import httpx
import jwt

LOGGER = logging.getLogger("litellm-gateway.mcp-pep")

ALLOW = "ALLOW"
DENY = "DENY"
STEP_UP_REQUIRED = "STEP_UP_REQUIRED"
EXPIRED = "EXPIRED"
LOA_BASELINE = 1
LOA_ELEVATED = 2

WRITE_TOOLS = frozenset(
    {"create_user", "delete_user_by_email", "update_user_by_email"}
)

# Mirrors infra/config/opa_policies/mcp_pep.rego — logged so the hop viewer
# can show the PDP rule without scraping OPA.
REQUIRED_SCOPES = {
    "list_all_users": ("users.read",),
    "search_users_by_first_name": ("users.read",),
    "create_user": ("users.write",),
    "delete_user_by_email": ("users.write",),
    "update_user_by_email": ("users.write",),
}
CIBA_TOOLS = frozenset({"create_user", "delete_user_by_email"})
PDP_PACKAGE = "mcp.pep"
PDP_PATH = "/v1/data/mcp/pep/decision"
PDP_POLICY = (
    "mcp.pep.decision: catalog allow-list (data.rules[source][dest].allow) "
    "+ required_scopes[tool] subset of JWT scope "
    "+ ciba_tools={create_user,delete_user_by_email}"
)

_CIBA_GRANT = "urn:openid:params:grant-type:ciba"

try:
    from litellm.integrations.custom_guardrail import CustomGuardrail
except ImportError:  # unit tests without the LiteLLM image
    CustomGuardrail = object  # type: ignore[misc,assignment]


class PepDenied(Exception):
    """Fail-closed tools/call. LiteLLM turns this into a guardrail block."""

    def __init__(self, message: str, *, error: str = "denied"):
        super().__init__(message)
        self.error = error
        self.message = message


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


def extract_identity(claims: dict[str, Any]) -> dict[str, Any]:
    scope_claim = claims.get("scope")
    if scope_claim is None:
        scp_claim = claims.get("scp")
        if isinstance(scp_claim, list):
            scope_claim = " ".join(str(s) for s in scp_claim)
        else:
            scope_claim = ""
    actor_claim = claims.get("act")
    agent_id = actor_claim.get("agent_id") if isinstance(actor_claim, dict) else None
    groups_claim = claims.get("groups")
    groups = groups_claim if isinstance(groups_claim, list) else []
    return {
        "preferred_username": claims.get("preferred_username"),
        "agent_id": agent_id,
        "scope": scope_claim,
        "sub": claims.get("sub"),
        "groups": groups,
        "raw": claims,
    }


def ciba_scope_for_tool(tool: str) -> str:
    return "openid users.write" if tool in WRITE_TOOLS else "openid users.read"


def _emit_audit(payload: dict[str, Any]) -> None:
    line = json.dumps(payload, ensure_ascii=True, default=str, separators=(",", ":"))
    print(line, flush=True)
    LOGGER.info("%s", line)


def _granted_scopes(scope: str | None) -> list[str]:
    return [s for s in str(scope or "").split() if s]


def _log_pdp_decision(
    *,
    tool_name: str,
    decision: str,
    current_loa: int,
    required_loa: int,
    request_id: str | None = None,
    enforce: str | None = None,
    **extra: Any,
) -> None:
    granted = extra.get("granted_scopes")
    if granted is None:
        granted = _granted_scopes(extra.get("scope"))
    payload = {
        "event": "pdp_decision",
        "tool": tool_name,
        "PDP_Decision": decision,
        "LoA_Level": current_loa,
        "Required_LoA": required_loa,
        "preferred_username": extra.get("preferred_username") or "-",
        "reason": extra.get("reason") or "-",
        "request_id": request_id or extra.get("request_id") or "-",
        "pep": "litellm-gateway/pdp_mcp.pre_mcp_call",
        "pdp": "opa-server",
        "pdp_package": PDP_PACKAGE,
        "pdp_path": extra.get("pdp_path") or PDP_PATH,
        "pdp_policy": PDP_POLICY,
        "enforce": enforce
        or extra.get("enforce")
        or (
            "inject_ciba_jwt"
            if current_loa >= LOA_ELEVATED
            else "deny"
            if decision in {DENY, EXPIRED}
            else "await_ciba"
            if decision == STEP_UP_REQUIRED
            else "inject_obo_jwt"
        ),
        "allow": extra.get("allow"),
        "ciba_required": extra.get("ciba_required"),
        "catalog_source": extra.get("catalog_source"),
        "catalog_dest": extra.get("catalog_dest"),
        "required_scopes": list(REQUIRED_SCOPES.get(tool_name, ())),
        "granted_scopes": list(granted) if granted is not None else [],
        "ciba_tool": tool_name in CIBA_TOOLS,
    }
    _emit_audit(payload)


class KeycloakJwtValidator:
    def __init__(
        self,
        jwks_url: str,
        audience: str,
        issuer: str,
        jwks_cache_seconds: int = 3600,
        algorithms: list[str] | None = None,
        leeway_seconds: int = 30,
    ):
        if not jwks_url or not audience or not issuer:
            raise PepDenied(
                "PEP_KEYCLOAK_JWKS_URL, PEP_MCP_AUDIENCE, and PEP_MCP_ISSUER "
                "are required for MCP tools/call.",
                error="configuration_error",
            )
        self._jwks_client = jwt.PyJWKClient(
            jwks_url,
            cache_keys=True,
            lifespan=jwks_cache_seconds,
        )
        self._audience = audience
        self._issuer = issuer
        self._algorithms = algorithms or ["RS256"]
        self._leeway = leeway_seconds

    def validate(self, token: str) -> dict[str, Any]:
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token).key
        except jwt.PyJWKClientError as exc:
            raise PepDenied(f"Unable to fetch signing key: {exc}", error="invalid_token") from exc
        except jwt.DecodeError as exc:
            raise PepDenied(f"Malformed token: {exc}", error="invalid_token") from exc
        try:
            return jwt.decode(
                token,
                signing_key,
                algorithms=self._algorithms,
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iat", "aud", "iss"]},
                leeway=self._leeway,
            )
        except jwt.ExpiredSignatureError as exc:
            raise PepDenied("Bearer token has expired.", error="expired_token") from exc
        except jwt.InvalidAudienceError as exc:
            raise PepDenied(
                "Token audience does not match this service.",
                error="invalid_audience",
            ) from exc
        except jwt.InvalidIssuerError as exc:
            raise PepDenied("Token issuer is not trusted.", error="invalid_issuer") from exc
        except jwt.InvalidTokenError as exc:
            raise PepDenied(f"Token is invalid: {exc}", error="invalid_token") from exc


class OpaPdp:
    """Query opa-server for the MCP tools/call decision."""

    def __init__(
        self,
        addr: str,
        decision_path: str = "/v1/data/mcp/pep/decision",
        timeout_seconds: float = 5.0,
    ):
        if not addr:
            raise PepDenied("PEP_OPA_URL is required.", error="configuration_error")
        path = decision_path if decision_path.startswith("/") else f"/{decision_path}"
        self._url = f"{addr.rstrip('/')}{path}"
        self._timeout = httpx.Timeout(timeout_seconds)

    async def decide(
        self,
        *,
        source: str,
        dest: str,
        tool: str,
        scope: str,
        user: str,
        groups: list[Any],
    ) -> dict[str, Any]:
        payload = {
            "input": {
                "source": source,
                "dest": dest,
                "tool": tool,
                "scope": scope,
                "user": user,
                "groups": groups,
            }
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(self._url, json=payload)
        except httpx.HTTPError as exc:
            raise PepDenied(
                f"OPA PDP failed (transport): {exc}",
                error="opa_unreachable",
            ) from exc
        if resp.status_code >= 400:
            raise PepDenied(
                f"OPA PDP rejected the query (status={resp.status_code})",
                error="opa_unreachable",
            )
        body = resp.json() if resp.content else {}
        result = body.get("result") if isinstance(body, dict) else None
        if not isinstance(result, dict):
            raise PepDenied(
                "OPA PDP returned no mcp.pep.decision document.",
                error="opa_unreachable",
            )
        return result


class CibaClient:
    def __init__(
        self,
        keycloak_url: str,
        realm: str,
        client_id: str,
        client_secret: str,
        poll_timeout_seconds: float = 110.0,
        approve_url: str = "http://localhost:8093",
        actor_token_path: str | None = None,
    ):
        base = keycloak_url.rstrip("/")
        self._token_url = f"{base}/realms/{realm}/protocol/openid-connect/token"
        self._auth_url = (
            f"{base}/realms/{realm}/protocol/openid-connect/ext/ciba/auth"
        )
        self._client_id = client_id
        self._client_secret = client_secret
        self._poll_timeout = poll_timeout_seconds
        self._approve_url = approve_url
        self._actor_token_path = Path(actor_token_path) if actor_token_path else None

    def _read_actor_token(self) -> str | None:
        if self._actor_token_path is None:
            return None
        try:
            token = self._actor_token_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            LOGGER.warning("event=ciba_actor_token_read_failed path=%s err=%s", self._actor_token_path, exc)
            return None
        return token or None

    async def fetch_access_token(
        self, login_hint: str, binding_message: str, scope: str
    ) -> str:
        if not self._client_id or not self._client_secret:
            raise PepDenied(
                "CIBA is required by OPA policy but PEP_CIBA_CLIENT_ID/"
                "PEP_CIBA_CLIENT_SECRET is not set.",
                error="configuration_error",
            )
        timeout = httpx.Timeout(20.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            start = await client.post(
                self._auth_url,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "login_hint": login_hint,
                    "scope": scope,
                    "binding_message": _binding_message(binding_message),
                },
            )
            body = _json(start)
            if start.status_code >= 400 or "auth_req_id" not in body:
                raise PepDenied(
                    f"Keycloak CIBA backchannel request failed (status={start.status_code})",
                    error="ciba_failed",
                )
            auth_req_id = str(body["auth_req_id"])
            interval = max(int(body.get("interval") or 5), 1)
            _emit_audit(
                {
                    "event": "ciba_started",
                    "login_hint": login_hint,
                    "binding_message": binding_message,
                    "tool": binding_message,
                    "approve_url": self._approve_url,
                    "pep": "litellm-gateway/pdp_mcp.CibaClient",
                    "pdp": "opa-server mcp.pep ciba_tools",
                    "enforce": "await_ciba",
                    "reason": "step-up",
                }
            )
            actor_token = self._read_actor_token()
            elapsed = 0.0
            while elapsed < self._poll_timeout:
                await asyncio.sleep(interval)
                elapsed += interval
                poll_data = {
                    "grant_type": _CIBA_GRANT,
                    "auth_req_id": auth_req_id,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                }
                if actor_token:
                    poll_data["delegation_actor"] = actor_token
                poll = await client.post(self._token_url, data=poll_data)
                token_body = _json(poll)
                if poll.status_code == 200 and token_body.get("access_token"):
                    _emit_audit(
                        {
                            "event": "ciba_approved",
                            "login_hint": login_hint,
                            "binding_message": binding_message,
                            "tool": binding_message,
                            "pep": "litellm-gateway/pdp_mcp.CibaClient",
                            "enforce": "inject_ciba_jwt",
                            "reason": "human-approved",
                        }
                    )
                    return str(token_body["access_token"])
                err = str(token_body.get("error") or "")
                if err == "slow_down":
                    interval += 5
                    continue
                if err == "authorization_pending":
                    continue
                if err in ("access_denied", "expired_token"):
                    decision = EXPIRED if err == "expired_token" else DENY
                    raise PepDenied(
                        "CIBA was denied or expired. Approve the request at "
                        f"{self._approve_url} and retry.",
                        error=decision.lower(),
                    )
                raise PepDenied(
                    f"Keycloak CIBA token poll failed (status={poll.status_code})",
                    error="ciba_failed",
                )
        raise PepDenied(
            "Timed out waiting for CIBA approval. Open "
            f"{self._approve_url}, approve the pending request, and retry.",
            error="expired",
        )


def _binding_message(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in text)
    cleaned = cleaned.strip("-")[:80]
    return cleaned or "write"


def _json(resp: httpx.Response) -> dict:
    try:
        body = resp.json()
    except ValueError:
        return {"raw": resp.text[:400]}
    return body if isinstance(body, dict) else {"raw": str(body)[:400]}


def extra_headers_for(*, jwt_token: str, decision: str, current_loa: int, required_loa: int) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {jwt_token}",
        "X-PEP-Decision": decision,
        "X-PEP-LoA": str(current_loa),
        "X-PEP-Required-LoA": str(required_loa),
    }


def _deny_from_opa(tool_name: str, reason: str) -> PepDenied:
    if reason == "insufficient_scope":
        return PepDenied(
            f"Tool '{tool_name}' requires additional OAuth scope.",
            error="insufficient_scope",
        )
    if reason == "catalog":
        return PepDenied(
            f"Tool '{tool_name}' is not allowed by the MCP catalog.",
            error="denied",
        )
    return PepDenied(f"Tool '{tool_name}' was denied by OPA ({reason}).", error="denied")


class McpPep:
    def __init__(
        self,
        *,
        source: str,
        dest: str,
        jwt_validator: Any,
        opa: OpaPdp,
        ciba: CibaClient,
    ):
        self._source = source
        self._dest = dest
        self._jwt_validator = jwt_validator
        self._opa = opa
        self._ciba = ciba

    @classmethod
    def from_env(cls) -> "McpPep":
        return cls(
            source=os.getenv("PEP_MCP_SOURCE", "default/litellm-gateway"),
            dest=os.getenv("PEP_MCP_DEST", "default/user-mcp"),
            jwt_validator=KeycloakJwtValidator(
                jwks_url=os.getenv("PEP_KEYCLOAK_JWKS_URL", ""),
                audience=os.getenv("PEP_MCP_AUDIENCE", "user-mcp"),
                issuer=os.getenv("PEP_MCP_ISSUER", ""),
            ),
            opa=OpaPdp(
                addr=os.getenv("PEP_OPA_URL", ""),
                decision_path=os.getenv(
                    "PEP_OPA_DECISION_PATH", "/v1/data/mcp/pep/decision"
                ),
            ),
            ciba=CibaClient(
                keycloak_url=os.getenv("PEP_CIBA_KEYCLOAK_URL", ""),
                realm=os.getenv("PEP_CIBA_REALM", "demo"),
                client_id=os.getenv("PEP_CIBA_CLIENT_ID", "ciba-client"),
                client_secret=os.getenv("PEP_CIBA_CLIENT_SECRET", ""),
                poll_timeout_seconds=float(os.getenv("PEP_CIBA_POLL_TIMEOUT_SECONDS", "110")),
                approve_url=os.getenv("PEP_CIBA_APPROVE_URL", "http://localhost:8093"),
                actor_token_path=os.getenv("PEP_ACTOR_TOKEN_PATH") or None,
            ),
        )

    async def authorize(
        self,
        tool_name: str,
        bearer_token: str | None,
        request_id: str | None = None,
    ) -> dict[str, str]:
        token = strip_bearer(bearer_token)
        if not token:
            raise PepDenied("Authorization bearer token is required.", error="invalid_request")
        if not tool_name:
            raise PepDenied("MCP tool name is required.", error="invalid_request")

        claims = self._jwt_validator.validate(token)
        identity = extract_identity(claims)
        user = identity.get("preferred_username") or ""
        if not user:
            raise PepDenied("Token is missing preferred_username.", error="invalid_token")

        result = await self._opa.decide(
            source=self._source,
            dest=self._dest,
            tool=tool_name,
            scope=identity.get("scope") or "",
            user=user,
            groups=list(identity.get("groups") or []),
        )
        reason = str(result.get("reason") or "default-deny")
        granted = _granted_scopes(identity.get("scope"))
        common = {
            "preferred_username": user,
            "reason": reason,
            "allow": bool(result.get("allow")),
            "ciba_required": bool(result.get("ciba_required")),
            "catalog_source": self._source,
            "catalog_dest": self._dest,
            "granted_scopes": granted,
            "scope": identity.get("scope") or "",
            "pdp_path": getattr(self._opa, "_url", PDP_PATH),
        }
        if not result.get("allow"):
            _log_pdp_decision(
                tool_name=tool_name,
                decision=DENY,
                current_loa=LOA_BASELINE,
                required_loa=LOA_BASELINE,
                request_id=request_id,
                enforce="deny",
                **common,
            )
            raise _deny_from_opa(tool_name, reason)

        if not result.get("ciba_required"):
            _log_pdp_decision(
                tool_name=tool_name,
                decision=ALLOW,
                current_loa=LOA_BASELINE,
                required_loa=LOA_BASELINE,
                request_id=request_id,
                enforce="inject_obo_jwt",
                **common,
            )
            return extra_headers_for(
                jwt_token=token,
                decision=ALLOW,
                current_loa=LOA_BASELINE,
                required_loa=LOA_BASELINE,
            )

        _log_pdp_decision(
            tool_name=tool_name,
            decision=STEP_UP_REQUIRED,
            current_loa=LOA_BASELINE,
            required_loa=LOA_ELEVATED,
            request_id=request_id,
            enforce="await_ciba",
            **common,
        )
        try:
            ciba_jwt = await self._ciba.fetch_access_token(
                login_hint=user,
                binding_message=tool_name,
                scope=ciba_scope_for_tool(tool_name),
            )
        except PepDenied as exc:
            decision = EXPIRED if exc.error in {"expired", EXPIRED.lower()} else DENY
            _log_pdp_decision(
                tool_name=tool_name,
                decision=decision,
                current_loa=LOA_BASELINE,
                required_loa=LOA_ELEVATED,
                request_id=request_id,
                enforce="deny",
                **common,
            )
            raise
        _log_pdp_decision(
            tool_name=tool_name,
            decision=ALLOW,
            current_loa=LOA_ELEVATED,
            required_loa=LOA_ELEVATED,
            request_id=request_id,
            enforce="inject_ciba_jwt",
            **common,
        )
        return extra_headers_for(
            jwt_token=ciba_jwt,
            decision=ALLOW,
            current_loa=LOA_ELEVATED,
            required_loa=LOA_ELEVATED,
        )


class McpPepGuardrail(CustomGuardrail):
    """LiteLLM pre_mcp_call hook: OPA catalog / scope / CIBA."""

    def __init__(self, pep: McpPep | None = None, **kwargs: Any):
        super().__init__(**kwargs)
        self._pep = pep

    def _pep_instance(self) -> McpPep:
        if self._pep is None:
            self._pep = McpPep.from_env()
        return self._pep

    async def async_pre_call_hook(
        self,
        user_api_key_dict: Any,
        cache: Any,
        data: dict,
        call_type: Any,
    ) -> Optional[dict]:
        payload = data or {}
        tool_name = payload.get("mcp_tool_name") or payload.get("name")
        if not tool_name:
            return payload
        bearer = payload.get("incoming_bearer_token") or _bearer_from_metadata(payload)
        request_id = _request_id_from_payload(payload)
        try:
            headers = await self._pep_instance().authorize(
                str(tool_name),
                bearer,
                request_id=request_id,
            )
        except PepDenied as exc:
            _emit_audit(
                {
                    "event": "pdp_decision",
                    "PDP_Decision": "DENY",
                    "tool": tool_name,
                    "error": exc.error,
                    "request_id": request_id or "-",
                    "pep": "litellm-gateway/pdp_mcp.pre_mcp_call",
                    "pdp": "opa-server",
                    "pdp_package": PDP_PACKAGE,
                    "pdp_path": PDP_PATH,
                    "pdp_policy": PDP_POLICY,
                    "enforce": "deny",
                    "reason": exc.message,
                }
            )
            raise Exception(exc.message) from exc
        return {"extra_headers": headers}


def _headers_from_payload(payload: dict) -> dict:
    metadata = payload.get("metadata") or {}
    headers = metadata.get("headers") if isinstance(metadata, dict) else None
    return headers if isinstance(headers, dict) else {}


def _bearer_from_metadata(payload: dict) -> str | None:
    headers = _headers_from_payload(payload)
    return headers.get("Authorization") or headers.get("authorization")


def _request_id_from_payload(payload: dict) -> str | None:
    headers = _headers_from_payload(payload)
    for key in ("x-request-id", "X-Request-ID", "X-Request-Id"):
        value = headers.get(key)
        if value:
            return str(value)
    for key in ("litellm_trace_id", "litellm_call_id", "request_id"):
        value = payload.get(key)
        if value:
            return str(value)
    return None
