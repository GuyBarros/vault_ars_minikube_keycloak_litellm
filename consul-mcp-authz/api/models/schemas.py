from typing import Annotated

from pydantic import BaseModel, Field, RootModel

# Tool name: short identifiers like "list_all_users".
ToolName = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_/.\-]+$")]

# Destination key: "<dest-ns>/<dest-svc>".
DestKey = Annotated[str, Field(pattern=r"^[a-z0-9-]+/[a-z0-9-]+$")]

# Source key: "<src-ns>/<src-svc>".
SrcKey = Annotated[str, Field(pattern=r"^[a-z0-9-]+/[a-z0-9-]+$")]


class PairAllow(BaseModel):
    allow: list[ToolName] = Field(default_factory=list)


# rules: { "<src>": { "<dst>": { "allow": [...] } } }
RulesMap = RootModel[dict[SrcKey, dict[DestKey, PairAllow]]]


class Catalog(BaseModel):
    """The full catalog document stored at opa-policies/data/mcp-authz/catalog."""

    rules: dict[SrcKey, dict[DestKey, PairAllow]] = Field(default_factory=dict)


class CatalogRead(BaseModel):
    """GET /v1/rules response — catalog plus KV v2 versioning metadata."""

    catalog: Catalog
    version: int
    created_time: str | None = None


class CatalogWriteRequest(BaseModel):
    """PUT /v1/rules request body."""

    catalog: Catalog
    # CAS: client passes the version they read; Vault rejects if it has moved.
    # Omit to write unconditionally (P1 pilot only — P1.5 will make this required).
    expected_version: int | None = None


class CatalogWriteResponse(BaseModel):
    version: int
    created_time: str | None = None


# ---------------------------------------------------------------------------
# PATCH /v1/rules/{src-ns}/{src-svc}/{dst-ns}/{dst-svc}
# ---------------------------------------------------------------------------


class PairPatchRequest(BaseModel):
    """Replace the `allow` list for one (source, destination) pair.

    The handler does a read-modify-write against the full catalog so the
    semantic-validation path and CAS guarantee stay the same as PUT.
    """

    allow: list[ToolName] = Field(default_factory=list)
    expected_version: int | None = None


# ---------------------------------------------------------------------------
# GET /v1/rules/history
# ---------------------------------------------------------------------------


class VersionInfo(BaseModel):
    version: int
    created_time: str | None = None
    deletion_time: str | None = None
    destroyed: bool = False


class HistoryResponse(BaseModel):
    current_version: int
    versions: list[VersionInfo]


# ---------------------------------------------------------------------------
# POST /v1/rules:rollback
# ---------------------------------------------------------------------------


class RollbackRequest(BaseModel):
    version: int = Field(ge=1)
    expected_version: int | None = None


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class ServiceRef(BaseModel):
    """A Consul service identified by {namespace, name}."""

    namespace: str
    name: str


class AgentList(BaseModel):
    agents: list[ServiceRef]


class McpServerList(BaseModel):
    mcp_servers: list[ServiceRef]


class ToolInfo(BaseModel):
    """One tool returned by an MCP server's `tools/list` response."""

    name: ToolName
    description: str | None = None


class McpServerTools(BaseModel):
    namespace: str
    name: str
    tools: list[ToolInfo]
    # Where the tool list came from. Currently always "mcp-tools-list"
    # (live `tools/list` RPC over the mesh); kept as a field so a
    # service-meta fallback could be added later without a breaking change.
    source: str = "mcp-tools-list"
