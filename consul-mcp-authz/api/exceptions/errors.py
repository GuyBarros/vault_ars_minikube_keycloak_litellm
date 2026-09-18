class CatalogError(Exception):
    """Base for all catalog-handling errors."""


class CatalogValidationError(CatalogError):
    """Candidate catalog failed OPA-side validation."""


class CatalogConflictError(CatalogError):
    """Concurrent edit: KV v2 CAS check failed (version mismatch)."""


class VaultUnavailableError(CatalogError):
    """Vault is unreachable or returned a transport-level error."""


class VaultAuthError(CatalogError):
    """API workload's Vault token is missing, expired, or forbidden."""


class CatalogNotFoundError(CatalogError):
    """No catalog entry exists for the requested (src,dst) pair."""


class DiscoveryError(Exception):
    """Base for Consul-discovery failures."""


class DiscoveryAuthError(DiscoveryError):
    """API workload's Consul token is missing, expired, or forbidden."""


class DiscoveryUnavailableError(DiscoveryError):
    """Consul is unreachable or returned a transport-level error."""


class McpDiscoveryError(DiscoveryError):
    """A target MCP server could not be queried for its tool list.

    Possible causes: mesh denies the call (ServiceIntention missing),
    OPA denies the call (catalog entry missing for the API), MCP server
    is down, MCP server requires auth and we sent none.
    """
