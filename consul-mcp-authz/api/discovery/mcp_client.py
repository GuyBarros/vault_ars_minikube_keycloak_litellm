"""Minimal MCP streamable-HTTP client for tool discovery.

Just enough of the protocol to do:

    initialize  →  notifications/initialized  →  tools/list  →  (drop session)

The session is not reused between calls; each discovery request opens a
new session. That's fine because:
  - The result is cached for `settings.discovery_cache_ttl_seconds`.
  - Discovery is operator-triggered, not on the hot data path.

Auth: this client sends no Authorization header. user-mcp accepts
unauthenticated `tools/list` when `USER_MCP_ALLOW_UNAUTH_DISCOVERY=true`,
because the mesh edge already authenticated the peer via mTLS and OPA
already gated the call. `tools/call` continues to require a real JWT —
this knob only affects metadata reads.

Preconditions for this client to work end-to-end:
  1. A Consul ServiceIntention from `consul-mcp-authz` to the target.
  2. A catalog entry `<src-ns>/consul-mcp-authz → <dst-ns>/<dst-svc>`
     with `allow: []` — `tools/list` is in the policy's `protocol_methods`
     set so an entry with an empty allow list is sufficient.
"""

from __future__ import annotations

import json
import time
from typing import Any

import requests

from app_logging.logger import get_logger
from config.settings import settings
from exceptions.errors import McpDiscoveryError

logger = get_logger(__name__)


class McpToolsDiscovery:
    """Discover the tool surface of an MCP server via `tools/list`.

    Owns the URL pattern, timeout, and per-`(namespace, name)` cache.
    """

    def __init__(self) -> None:
        self._pattern = settings.mcp_url_pattern
        self._timeout = settings.mcp_timeout_seconds
        self._cache_ttl = settings.discovery_cache_ttl_seconds
        self._cache: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}

    def _url_for(self, namespace: str, name: str) -> str:
        return self._pattern.format(namespace=namespace, name=name)

    def _cache_get(self, key: tuple[str, str]) -> list[dict[str, Any]] | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        deadline, tools = entry
        if time.monotonic() > deadline:
            self._cache.pop(key, None)
            return None
        return tools

    def _cache_put(self, key: tuple[str, str], tools: list[dict[str, Any]]) -> None:
        self._cache[key] = (time.monotonic() + self._cache_ttl, tools)

    def invalidate(self) -> None:
        self._cache.clear()

    # ------------------------------------------------------------------
    # Protocol
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_response(resp: requests.Response) -> dict[str, Any]:
        """Parse a JSON-RPC response from either application/json or SSE.

        FastMCP can return either; we accept both. For SSE, the first
        `data:` event carries the JSON-RPC envelope.
        """
        content_type = (resp.headers.get("content-type") or "").lower()
        if "application/json" in content_type:
            return resp.json()
        if "text/event-stream" in content_type:
            for line in resp.text.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[5:].strip())
            raise McpDiscoveryError("SSE response carried no data: event")
        # Some servers omit content-type; try JSON as a last resort.
        try:
            return resp.json()
        except ValueError as exc:
            raise McpDiscoveryError(
                f"unexpected MCP content-type {content_type!r}: {exc}"
            ) from exc

    def tools_for(self, namespace: str, name: str) -> list[dict[str, Any]]:
        """Return tools as `[{name, description}]`. Description may be ``None``
        when the MCP server omits it (the field is optional in the protocol)."""
        cached = self._cache_get((namespace, name))
        if cached is not None:
            return cached

        url = self._url_for(namespace, name)
        common_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

        # 1. initialize — opens the session.
        init_body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "consul-mcp-authz", "version": "0.1.0"},
            },
        }
        try:
            resp = requests.post(
                url, headers=common_headers, json=init_body, timeout=self._timeout
            )
        except requests.RequestException as exc:
            raise McpDiscoveryError(f"initialize transport failure for {url}: {exc}") from exc
        if resp.status_code >= 400:
            raise McpDiscoveryError(
                f"initialize {url} returned {resp.status_code} "
                f"(authz-reason={resp.headers.get('x-authz-reason')!r}): "
                f"{resp.text[:200]}"
            )

        session_id = resp.headers.get("mcp-session-id") or resp.headers.get(
            "Mcp-Session-Id"
        )
        if not session_id:
            raise McpDiscoveryError(
                f"initialize response from {url} missing Mcp-Session-Id header"
            )

        session_headers = {**common_headers, "Mcp-Session-Id": session_id}

        # 2. notifications/initialized — required handshake completion;
        #    no response is expected, but we still POST it.
        notif_body = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        try:
            requests.post(
                url, headers=session_headers, json=notif_body, timeout=self._timeout
            )
        except requests.RequestException as exc:
            raise McpDiscoveryError(
                f"notifications/initialized transport failure for {url}: {exc}"
            ) from exc

        # 3. tools/list — the payload we actually care about.
        list_body = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        try:
            resp = requests.post(
                url, headers=session_headers, json=list_body, timeout=self._timeout
            )
        except requests.RequestException as exc:
            raise McpDiscoveryError(f"tools/list transport failure for {url}: {exc}") from exc
        if resp.status_code >= 400:
            raise McpDiscoveryError(
                f"tools/list {url} returned {resp.status_code} "
                f"(authz-reason={resp.headers.get('x-authz-reason')!r}): "
                f"{resp.text[:200]}"
            )

        payload = self._parse_response(resp)
        if "error" in payload:
            raise McpDiscoveryError(f"tools/list error from {url}: {payload['error']}")
        tools_field = payload.get("result", {}).get("tools", [])
        tools = [
            {"name": t["name"], "description": t.get("description")}
            for t in tools_field
            if isinstance(t, dict) and "name" in t
        ]

        self._cache_put((namespace, name), tools)
        return tools
