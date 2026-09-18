"""Read-only Consul catalog client used for participant discovery.

Workloads declare their role in MCP authz via a single Consul service-meta
annotation:

    consul.hashicorp.com/service-meta-agent-tool-authz-role: "agent"
    consul.hashicorp.com/service-meta-agent-tool-authz-role: "mcp-server"

Consul-K8s translates this into a ServiceMeta entry on the registered
service. We read it back via the catalog HTTP API to populate
`GET /v1/agents` and `GET /v1/mcp-servers`.

The *tools* a server exposes are NOT carried here — they are discovered
live via MCP `tools/list` over the mesh (see discovery/mcp_client.py).
That avoids drift between the deployment manifest and the running server.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import requests

from app_logging.logger import get_logger
from config.settings import settings
from exceptions.errors import (
    DiscoveryAuthError,
    DiscoveryUnavailableError,
)

logger = get_logger(__name__)


class ConsulCatalogClient:
    """Read-only wrapper over Consul's HTTP catalog API.

    Auth: ACL token rendered to disk by the vault-agent sidecar. Re-read
    per request, so token rotation is transparent.
    """

    def __init__(self) -> None:
        self._addr = settings.consul_addr.rstrip("/")
        self._token_path = Path(settings.consul_token_path)
        self._verify: bool | str = (
            settings.consul_ca_bundle
            if settings.consul_ca_bundle
            else settings.consul_tls_verify
        )
        self._ns = settings.consul_namespace
        self._role_key = settings.consul_meta_role_key
        self._cache_ttl = settings.discovery_cache_ttl_seconds
        # cache: key -> (deadline_monotonic, value)
        self._cache: dict[str, tuple[float, Any]] = {}

    # ------------------------------------------------------------------
    # Auth + transport
    # ------------------------------------------------------------------

    def _token(self) -> str:
        try:
            token = self._token_path.read_text().strip()
        except OSError as exc:
            raise DiscoveryAuthError(
                f"Consul token file not readable at {self._token_path}: {exc}"
            ) from exc
        if not token:
            raise DiscoveryAuthError(
                f"Consul token file at {self._token_path} is empty"
            )
        return token

    def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        url = f"{self._addr}{path}"
        headers = {"X-Consul-Token": self._token()}
        try:
            resp = requests.get(
                url,
                headers=headers,
                params=params,
                verify=self._verify,
                timeout=5,
            )
        except requests.RequestException as exc:
            raise DiscoveryUnavailableError(
                f"Consul GET {path} failed: {exc}"
            ) from exc

        if resp.status_code == 401 or resp.status_code == 403:
            raise DiscoveryAuthError(
                f"Consul rejected the API token (status {resp.status_code}): {resp.text[:200]}"
            )
        if resp.status_code >= 400:
            raise DiscoveryUnavailableError(
                f"Consul GET {path} returned {resp.status_code}: {resp.text[:200]}"
            )
        return resp.json()

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    def _cache_get(self, key: str) -> Any | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        deadline, value = entry
        if time.monotonic() > deadline:
            self._cache.pop(key, None)
            return None
        return value

    def _cache_put(self, key: str, value: Any) -> None:
        self._cache[key] = (time.monotonic() + self._cache_ttl, value)

    def invalidate(self) -> None:
        self._cache.clear()

    # ------------------------------------------------------------------
    # Discovery API
    # ------------------------------------------------------------------

    def _list_services_with_meta(self) -> list[dict[str, Any]]:
        """Return one instance record per (namespace, service-name).

        We need per-instance ServiceMeta to filter on `agent-tool-authz-role`,
        so we iterate `/v1/catalog/services` then `/v1/catalog/service/<name>`.
        Result is cached for `discovery_cache_ttl_seconds`.
        """
        cached = self._cache_get("services_with_meta")
        if cached is not None:
            return cached

        list_params: dict[str, str] = {}
        if self._ns:
            list_params["ns"] = self._ns
        services = self._get("/v1/catalog/services", params=list_params)
        out: list[dict[str, Any]] = []
        if not isinstance(services, dict):
            self._cache_put("services_with_meta", out)
            return out

        for name in services.keys():
            try:
                params: dict[str, str] = {}
                if self._ns:
                    params["ns"] = self._ns
                instances = self._get(f"/v1/catalog/service/{name}", params=params)
            except DiscoveryUnavailableError as exc:
                # One service failing should not fail the whole discovery.
                logger.warning("consul_service_read_failed", service=name, error=str(exc))
                continue
            if not isinstance(instances, list):
                continue
            seen_ns: set[str] = set()
            for inst in instances:
                # Skip Envoy sidecars and gateways — Consul connect-inject
                # copies service-meta from the parent pod onto the sidecar
                # proxy registration, so both `foo` and `foo-sidecar-proxy`
                # would otherwise match `agent-tool-authz-role`.
                if inst.get("ServiceKind"):
                    continue
                ns = inst.get("Namespace") or "default"
                if ns in seen_ns:
                    continue
                seen_ns.add(ns)
                meta = inst.get("ServiceMeta") or {}
                out.append({
                    "namespace": ns,
                    "name": inst.get("ServiceName") or name,
                    "meta": meta,
                })
        self._cache_put("services_with_meta", out)
        return out

    def list_by_role(self, role: str) -> list[dict[str, str]]:
        """Return [{namespace, name}] for services with `agent-tool-authz-role == role`."""
        items = self._list_services_with_meta()
        out: list[dict[str, str]] = []
        for svc in items:
            if svc["meta"].get(self._role_key) == role:
                out.append({"namespace": svc["namespace"], "name": svc["name"]})
        # Stable sort for predictable UI ordering.
        out.sort(key=lambda s: (s["namespace"], s["name"]))
        return out

    def has_role(self, namespace: str, name: str, role: str) -> bool:
        """True iff `<namespace>/<name>` is registered in Consul with `role`."""
        items = self._list_services_with_meta()
        for svc in items:
            if svc["namespace"] == namespace and svc["name"] == name:
                return svc["meta"].get(self._role_key) == role
        return False
