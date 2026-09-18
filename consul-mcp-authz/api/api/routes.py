from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app_logging.logger import get_logger
from catalog.validator import validate
from catalog.vault_client import VaultCatalogClient
from discovery.consul_client import ConsulCatalogClient
from discovery.mcp_client import McpToolsDiscovery
from exceptions.errors import (
    CatalogConflictError,
    CatalogNotFoundError,
    CatalogValidationError,
    DiscoveryAuthError,
    DiscoveryUnavailableError,
    McpDiscoveryError,
    VaultAuthError,
    VaultUnavailableError,
)
from models.schemas import (
    AgentList,
    Catalog,
    CatalogRead,
    CatalogWriteRequest,
    CatalogWriteResponse,
    HistoryResponse,
    McpServerList,
    McpServerTools,
    PairPatchRequest,
    RollbackRequest,
    ServiceRef,
    VersionInfo,
)

router = APIRouter()
logger = get_logger(__name__)

_vault = VaultCatalogClient()
_consul = ConsulCatalogClient()
_mcp_tools = McpToolsDiscovery()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _vault_error_response(exc: Exception, op: str) -> JSONResponse:
    """Map Vault layer errors to HTTP responses (used by every route)."""
    if isinstance(exc, CatalogConflictError):
        logger.info(f"{op}_conflict", error=str(exc))
        return JSONResponse(
            status_code=412,
            content={"detail": "Catalog has been updated since you read it; re-fetch and retry."},
        )
    if isinstance(exc, VaultAuthError):
        logger.warning(f"{op}_auth_failure", error=str(exc))
        return JSONResponse(status_code=401, content={"detail": str(exc)})
    if isinstance(exc, VaultUnavailableError):
        logger.error(f"{op}_vault_unavailable", error=str(exc))
        return JSONResponse(status_code=503, content={"detail": "Vault is unavailable"})
    raise exc  # propagate to FastAPI's unhandled handler


def _discovery_error_response(exc: Exception, op: str) -> JSONResponse:
    if isinstance(exc, DiscoveryAuthError):
        logger.warning(f"{op}_auth_failure", error=str(exc))
        return JSONResponse(
            status_code=503,
            content={"detail": f"Consul discovery is not configured: {exc}"},
        )
    if isinstance(exc, DiscoveryUnavailableError):
        logger.error(f"{op}_unavailable", error=str(exc))
        return JSONResponse(
            status_code=503,
            content={"detail": "Consul is unavailable"},
        )
    raise exc


# ---------------------------------------------------------------------------
# Catalog: GET / PUT
# ---------------------------------------------------------------------------


@router.get("/v1/rules", response_model=CatalogRead)
async def get_rules(request: Request) -> CatalogRead | JSONResponse:
    try:
        catalog_dict, version, created_time = _vault.read()
    except (VaultAuthError, VaultUnavailableError) as exc:
        return _vault_error_response(exc, "rules_read")

    return CatalogRead(
        catalog=Catalog(**catalog_dict),
        version=version,
        created_time=created_time,
    )


@router.put("/v1/rules", response_model=CatalogWriteResponse)
async def put_rules(request: Request, body: CatalogWriteRequest) -> CatalogWriteResponse | JSONResponse:
    candidate = body.catalog.model_dump()

    try:
        validate(candidate)
    except CatalogValidationError as exc:
        logger.warning("rules_write_validation_failed", error=str(exc))
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    try:
        version, created_time = _vault.write(candidate, body.expected_version)
    except (CatalogConflictError, VaultAuthError, VaultUnavailableError) as exc:
        return _vault_error_response(exc, "rules_write")

    logger.info(
        "rules_written",
        version=version,
        n_sources=len(candidate.get("rules", {})),
    )
    return CatalogWriteResponse(version=version, created_time=created_time)


# ---------------------------------------------------------------------------
# Catalog: PATCH one pair
# ---------------------------------------------------------------------------


@router.patch(
    "/v1/rules/{src_ns}/{src_svc}/{dst_ns}/{dst_svc}",
    response_model=CatalogWriteResponse,
)
async def patch_pair(
    request: Request,
    src_ns: str,
    src_svc: str,
    dst_ns: str,
    dst_svc: str,
    body: PairPatchRequest,
) -> CatalogWriteResponse | JSONResponse:
    """Replace the allow list for one (source, destination) pair.

    Read-modify-write under the same CAS contract as PUT — if
    expected_version is given and the catalog has moved, returns 412.
    """
    src_key = f"{src_ns}/{src_svc}"
    dst_key = f"{dst_ns}/{dst_svc}"

    try:
        current_dict, current_version, _ = _vault.read()
    except (VaultAuthError, VaultUnavailableError) as exc:
        return _vault_error_response(exc, "rules_patch_read")

    # If the caller passed expected_version, enforce it BEFORE we mutate
    # locally — gives a fast CAS-mismatch path without a Vault write.
    if body.expected_version is not None and body.expected_version != current_version:
        logger.info(
            "rules_patch_stale",
            expected=body.expected_version,
            current=current_version,
        )
        return JSONResponse(
            status_code=412,
            content={"detail": "Catalog has been updated since you read it; re-fetch and retry."},
        )

    # Validate the pair keys against the Pydantic schema by routing through
    # the full Catalog model — this catches malformed ns/svc segments and
    # bad tool names with the same rules as PUT/422.
    rules = dict(current_dict.get("rules", {}))
    src_bucket = dict(rules.get(src_key, {}))
    src_bucket[dst_key] = {"allow": list(body.allow)}
    rules[src_key] = src_bucket
    candidate = {"rules": rules}

    try:
        Catalog(**candidate)  # raises pydantic ValidationError → caught by FastAPI as 422
    except Exception as exc:  # narrow re-raise so FastAPI returns 422
        logger.warning("rules_patch_schema_invalid", error=str(exc))
        return JSONResponse(
            status_code=422,
            content={"detail": f"Patched catalog failed schema validation: {exc}"},
        )

    try:
        validate(candidate)
    except CatalogValidationError as exc:
        logger.warning("rules_patch_validation_failed", error=str(exc))
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    try:
        version, created_time = _vault.write(candidate, current_version)
    except (CatalogConflictError, VaultAuthError, VaultUnavailableError) as exc:
        return _vault_error_response(exc, "rules_patch_write")

    logger.info(
        "rules_pair_patched",
        src=src_key,
        dst=dst_key,
        version=version,
        n_allowed_tools=len(body.allow),
    )
    return CatalogWriteResponse(version=version, created_time=created_time)


# ---------------------------------------------------------------------------
# History + rollback
# ---------------------------------------------------------------------------


@router.get("/v1/rules/history", response_model=HistoryResponse)
async def get_history(request: Request) -> HistoryResponse | JSONResponse:
    try:
        current, versions = _vault.list_versions()
    except (VaultAuthError, VaultUnavailableError) as exc:
        return _vault_error_response(exc, "rules_history")

    items = [
        VersionInfo(
            version=v,
            created_time=info.get("created_time"),
            deletion_time=info.get("deletion_time"),
            destroyed=info.get("destroyed", False),
        )
        for v, info in sorted(versions.items(), reverse=True)
    ]
    return HistoryResponse(current_version=current, versions=items)


@router.post("/v1/rules:rollback", response_model=CatalogWriteResponse)
async def rollback(
    request: Request, body: RollbackRequest
) -> CatalogWriteResponse | JSONResponse:
    """Read the catalog at `version` and write it as a new version.

    KV v2 does not support in-place reverts; the canonical pattern is
    "read old, write new." The new version has a new number; the older
    versions stay readable via /v1/rules/history.
    """
    try:
        target_dict, target_version, _ = _vault.read_version(body.version)
    except (VaultAuthError, VaultUnavailableError) as exc:
        return _vault_error_response(exc, "rules_rollback_read")

    # Re-validate the target version against the *current* policy —
    # an old catalog could fail today's schema if the policy changed.
    try:
        Catalog(**target_dict)
    except Exception as exc:
        logger.warning(
            "rules_rollback_target_schema_invalid",
            target_version=target_version,
            error=str(exc),
        )
        return JSONResponse(
            status_code=409,
            content={"detail": f"Target version {target_version} does not satisfy current schema: {exc}"},
        )

    try:
        validate(target_dict)
    except CatalogValidationError as exc:
        logger.warning(
            "rules_rollback_target_validation_failed",
            target_version=target_version,
            error=str(exc),
        )
        return JSONResponse(
            status_code=409,
            content={"detail": f"Target version {target_version} no longer validates: {exc}"},
        )

    try:
        version, created_time = _vault.write(target_dict, body.expected_version)
    except (CatalogConflictError, VaultAuthError, VaultUnavailableError) as exc:
        return _vault_error_response(exc, "rules_rollback_write")

    logger.info(
        "rules_rolled_back",
        target_version=target_version,
        new_version=version,
    )
    return CatalogWriteResponse(version=version, created_time=created_time)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@router.get("/v1/agents", response_model=AgentList)
async def list_agents(request: Request) -> AgentList | JSONResponse:
    try:
        items = _consul.list_by_role("agent")
    except (DiscoveryAuthError, DiscoveryUnavailableError) as exc:
        return _discovery_error_response(exc, "discovery_agents")
    return AgentList(agents=[ServiceRef(**s) for s in items])


@router.get("/v1/mcp-servers", response_model=McpServerList)
async def list_mcp_servers(request: Request) -> McpServerList | JSONResponse:
    try:
        items = _consul.list_by_role("mcp-server")
    except (DiscoveryAuthError, DiscoveryUnavailableError) as exc:
        return _discovery_error_response(exc, "discovery_mcp_servers")
    return McpServerList(mcp_servers=[ServiceRef(**s) for s in items])


@router.get("/v1/mcp-servers/{ns}/{name}/tools", response_model=McpServerTools)
async def get_mcp_server_tools(
    request: Request, ns: str, name: str
) -> McpServerTools | JSONResponse:
    # First confirm the target is actually a Consul-registered mcp-server.
    # This produces a clear 404 instead of a confusing 502 when the URL is
    # for a workload that doesn't exist or isn't annotated as mcp-server.
    try:
        is_mcp_server = _consul.has_role(ns, name, "mcp-server")
    except (DiscoveryAuthError, DiscoveryUnavailableError) as exc:
        return _discovery_error_response(exc, "discovery_tools_consul")

    if not is_mcp_server:
        return JSONResponse(
            status_code=404,
            content={
                "detail": (
                    f"No mcp-server '{ns}/{name}' in Consul, or its pod is missing "
                    f"the `agent-tool-authz-role=mcp-server` service-meta annotation."
                )
            },
        )

    try:
        tools = _mcp_tools.tools_for(ns, name)
    except McpDiscoveryError as exc:
        logger.warning("discovery_tools_mcp_failed", target=f"{ns}/{name}", error=str(exc))
        return JSONResponse(
            status_code=502,
            content={
                "detail": (
                    f"Could not query MCP server '{ns}/{name}' for its tool list. "
                    f"Check (a) ServiceIntention consul-mcp-authz → {name}, "
                    f"(b) catalog entry default/consul-mcp-authz → {ns}/{name} "
                    f"with allow:[], (c) the MCP server is healthy. Error: {exc}"
                )
            },
        )

    return McpServerTools(namespace=ns, name=name, tools=tools, source="mcp-tools-list")


@router.get("/healthz")
async def health() -> dict:
    return {"status": "ok"}
