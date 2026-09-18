from __future__ import annotations

import logging
from contextvars import ContextVar
from datetime import timedelta
from typing import Any

import httpx

from errors import AppError
from logging_utils import log_event

LOGGER = logging.getLogger("agent_api.mcp_client")

# Set by invoke_mcp_tool right before it returns, task-local like
# postgres_repo.py's own _last_assurance - scoped_tool.py reads it
# immediately after awaiting the call, in the same async task, so there is
# no cross-request leakage despite this being module-level state.
_last_tool_meta: ContextVar[dict[str, Any] | None] = ContextVar(
    "_last_tool_meta", default=None
)


def get_last_tool_meta() -> dict[str, Any] | None:
    """The upstream MCP CallToolResult's `_meta` from the most recent
    invoke_mcp_tool call in this task - e.g. {"assurance": {...}} from
    user-mcp's PDP decision (see user-mcp/tools/users.py's _run_tool).
    langchain-mcp-adapters' own tool-call path silently drops `_meta`
    (confirmed by reading _convert_call_tool_result), so invoke_mcp_tool
    talks to the raw MCP session instead of going through it, specifically
    to keep this available."""
    return _last_tool_meta.get()

# Header set by the OPA/Envoy ext_authz sidecar in front of user-mcp when a
# request is denied — see deploy-k8s/agent-mcp-authz/policy/mcp/authz/mcp_authz.rego.
AUTHZ_REASON_HEADER = "x-authz-reason"
LITELLM_API_KEY_HEADER = "x-litellm-api-key"
# LiteLLM's MCP gateway prefixes every tools/list name with `{server}-`.
# The agent, SYSTEM_PROMPT, OBO scopes, and user-mcp all use the upstream
# names (list_all_users, …). Strip on discovery; /user_mcp/mcp still
# accepts the unprefixed name on tools/call.
LITELLM_MCP_TOOL_PREFIX = "user_mcp-"

# Fallback when LiteLLM drops FastMCP `_meta.required_scopes`. Keep in
# lockstep with user-mcp/tools/users.py TOOL_SCOPE_REQUIREMENTS.
KNOWN_TOOL_SCOPES: dict[str, list[str]] = {
    "list_all_users": ["users.read"],
    "search_users_by_first_name": ["users.read"],
    "create_user": ["users.write"],
    "delete_user_by_email": ["users.write"],
    "update_user_by_email": ["users.write"],
}


def mcp_request_headers(
    request_id: str,
    obo_token: str | None = None,
    litellm_api_key: str | None = None,
) -> dict[str, str]:
    """Headers for the streamable-HTTP MCP hop.

    When USER_MCP_URL points at LiteLLM's MCP gateway, the PEP needs
    `x-litellm-api-key`. Authorization stays the OBO so true_passthrough
    can forward it to user-mcp for JWT / CIBA. Discovery sends no
    Authorization.
    """
    headers: dict[str, str] = {"X-Request-ID": request_id}
    if obo_token:
        headers["Authorization"] = f"Bearer {obo_token}"
    if litellm_api_key:
        headers[LITELLM_API_KEY_HEADER] = f"Bearer {litellm_api_key}"
    return headers


def _find_authz_denied_response(
    exc: BaseException,
    seen: set[int] | None = None,
) -> httpx.Response | None:
    """Walk an exception tree (cause/context and ExceptionGroup members) for an
    httpx 403 carrying an `x-authz-reason` header. The MCP streamable_http
    transport runs requests inside an anyio task group, so the original
    HTTPStatusError can surface wrapped in a BaseExceptionGroup."""
    if seen is None:
        seen = set()
    if id(exc) in seen:
        return None
    seen.add(id(exc))

    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        if response.status_code == 403 and response.headers.get(AUTHZ_REASON_HEADER):
            return response

    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            found = _find_authz_denied_response(sub, seen)
            if found is not None:
                return found

    for chained in (exc.__cause__, exc.__context__):
        if chained is not None:
            found = _find_authz_denied_response(chained, seen)
            if found is not None:
                return found

    return None


def _raise_if_authz_denied(
    exc: BaseException,
    request_id: str,
    tool_name: str | None,
) -> None:
    response = _find_authz_denied_response(exc)
    if response is None:
        return
    reason = response.headers.get(AUTHZ_REASON_HEADER, "forbidden")
    log_event(
        LOGGER,
        "mcp_authz_denied",
        level=logging.WARNING,
        message=f"MCP request denied by ext_authz: {reason}",
        request_id=request_id,
        tool_name=tool_name,
        authz_reason=reason,
    )
    raise AppError(status_code=403, error="forbidden", message=reason) from exc


def _import_multi_server_client():
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError as exc:  # pragma: no cover - dep is required in prod
        raise RuntimeError(
            "langchain-mcp-adapters is required to load MCP tools. "
            "Install it via `uv sync`."
        ) from exc
    return MultiServerMCPClient


async def fetch_mcp_tools(
    user_mcp_url: str,
    obo_token: str | None,
    request_id: str,
    timeout_seconds: float = 30.0,
    litellm_api_key: str | None = None,
) -> list[Any]:
    """Build a fresh MCP client whose streamable-HTTP requests carry the
    given OBO bearer (if any) and the caller's X-Request-ID, then return
    the LangChain tool wrappers.

    A fresh client per request avoids cross-thread ContextVar propagation
    problems when LangChain's sync `tool.invoke()` bridges into the
    adapter's async transport.
    """
    MultiServerMCPClient = _import_multi_server_client()

    headers = mcp_request_headers(
        request_id, obo_token=obo_token, litellm_api_key=litellm_api_key
    )

    client = MultiServerMCPClient(
        {
            "user-mcp": {
                "url": user_mcp_url,
                "transport": "streamable_http",
                "headers": headers,
                "timeout": timedelta(seconds=timeout_seconds),
            }
        }
    )
    tools = await client.get_tools()
    for tool in tools:
        raw_name = getattr(tool, "name", None)
        canonical = canonical_mcp_tool_name(raw_name)
        if canonical and canonical != raw_name:
            tool.name = canonical
        tool_name = getattr(tool, "name", None)
        log_event(
            LOGGER,
            "mcp_tool_loaded",
            message=f"Loaded MCP tool {tool_name}",
            request_id=request_id,
            user_mcp_url=user_mcp_url,
            authorization_present=bool(obo_token),
            tool_name=tool_name,
        )
    return list(tools)


def canonical_mcp_tool_name(name: str | None) -> str:
    if not name:
        return ""
    if name.startswith(LITELLM_MCP_TOOL_PREFIX):
        return name[len(LITELLM_MCP_TOOL_PREFIX) :]
    return name


def extract_required_scopes(template_tool: Any) -> list[str]:
    """Read `_meta.required_scopes` from a langchain-mcp-adapters tool.

    The adapter places the upstream MCP `_meta` field under the LangChain
    tool's `metadata["_meta"]` (see langchain_mcp_adapters.tools._convert_call_tool_result).
    LiteLLM's MCP gateway often drops `_meta`; fall back to the known
    user-mcp contract keyed by the unprefixed tool name.
    """
    metadata = getattr(template_tool, "metadata", None) or {}
    if isinstance(metadata, dict):
        meta = metadata.get("_meta") or {}
        if isinstance(meta, dict):
            scopes = meta.get("required_scopes")
            if isinstance(scopes, list):
                return [str(s) for s in scopes]
    name = canonical_mcp_tool_name(getattr(template_tool, "name", None))
    return list(KNOWN_TOOL_SCOPES.get(name, []))


def _import_convert_call_tool_result():
    try:
        from langchain_mcp_adapters.tools import _convert_call_tool_result
    except ImportError as exc:  # pragma: no cover - dep is required in prod
        raise RuntimeError(
            "langchain-mcp-adapters is required to load MCP tools. "
            "Install it via `uv sync`."
        ) from exc
    return _convert_call_tool_result


async def invoke_mcp_tool(
    user_mcp_url: str,
    tool_name: str,
    args: dict,
    obo_token: str,
    request_id: str,
    timeout_seconds: float = 30.0,
    litellm_api_key: str | None = None,
) -> Any:
    """Build a transient MCP client carrying *obo_token* and call a single
    tool by name. Used by the per-call scope wrapper so each MCP tools/call
    travels with its own narrowly-scoped OBO.

    *timeout_seconds* must exceed however long the slowest tool on the
    other end can legitimately block — e.g. create_user's CIBA poll wait —
    or this call gets cut off before user-mcp responds. The library default
    is 30s, which is too short for that tool.

    Talks to the raw MCP ClientSession instead of going through
    langchain_mcp_adapters' StructuredTool wrapper (as this used to, via
    `target.ainvoke(args)`) so the CallToolResult's `_meta` survives - the
    adapter's own conversion drops it entirely. Content is still converted
    with the adapter's own `_convert_call_tool_result`, so the LLM-facing
    shape (and error behavior on isError) is byte-for-byte the same as
    before; only the extra `_last_tool_meta` capture is new.
    """
    MultiServerMCPClient = _import_multi_server_client()
    convert_call_tool_result = _import_convert_call_tool_result()

    headers = mcp_request_headers(
        request_id, obo_token=obo_token, litellm_api_key=litellm_api_key
    )
    client = MultiServerMCPClient(
        {
            "user-mcp": {
                "url": user_mcp_url,
                "transport": "streamable_http",
                "headers": headers,
                "timeout": timedelta(seconds=timeout_seconds),
            }
        }
    )
    _last_tool_meta.set(None)
    try:
        async with client.session("user-mcp") as session:
            result = await session.call_tool(tool_name, args)
        content, _artifact = convert_call_tool_result(result)
        _last_tool_meta.set(result.meta)
        return content
    except AppError:
        raise
    except BaseException as exc:
        _raise_if_authz_denied(exc, request_id=request_id, tool_name=tool_name)
        raise


