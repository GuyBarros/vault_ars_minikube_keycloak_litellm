from pydantic_settings import BaseSettings, SettingsConfigDict

from app_logging.logger import get_logger

logger = get_logger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MCP_AUTHZ_API_",
        case_sensitive=False,
        env_file=".env",
        extra="ignore",
    )

    vault_addr: str = "https://vault.vault.svc:8200"
    vault_tls_verify: bool = True
    vault_ca_bundle: str | None = None

    # Vault Agent sidecar renders the API's Vault token to this path.
    vault_token_path: str = "/vault/secrets/token"

    # KV v2 mount and the logical path within it.
    vault_kv_mount: str = "opa-policies"
    vault_kv_path: str = "mcp-authz/catalog"

    # Validator runs `opa eval` against the candidate JSON using these.
    opa_bin: str = "/usr/local/bin/opa"
    policy_dir: str = "/app/policy"

    # --- Discovery (Consul) ---
    # Consul HTTP API address. With Consul Enterprise + auto-encrypt this
    # is usually the external server LB on :8501 (HTTPS).
    consul_addr: str = "https://consul.service.consul:8501"
    consul_tls_verify: bool = True
    consul_ca_bundle: str | None = None
    # Vault Agent renders a Consul ACL token (service:read, namespace:read)
    # to this path. Discovery endpoints return 503 when the file is missing
    # or empty — discovery is opt-in.
    consul_token_path: str = "/vault/secrets/consul-token"
    # Optional: restrict service-meta scan to one Consul namespace.
    # Empty string = scan all namespaces the token can read.
    consul_namespace: str = ""
    # Annotation key (Consul service-meta). On a K8s pod this is set via
    # `consul.hashicorp.com/service-meta-<key>`. The value identifies the
    # workload's role in MCP authz: "agent" or "mcp-server".
    consul_meta_role_key: str = "agent-tool-authz-role"
    # In-memory discovery cache TTL (applies to both Consul catalog reads
    # and per-server MCP tools/list results).
    discovery_cache_ttl_seconds: int = 300

    # --- Discovery (MCP tools/list) ---
    # URL template for reaching MCP servers via the Consul service mesh.
    # `{name}` and `{namespace}` are formatted in. Override per-environment
    # if your mesh uses a different DNS form.
    mcp_url_pattern: str = "http://{name}.virtual.consul/mcp"
    mcp_timeout_seconds: int = 10

    log_level: str = "INFO"

    def log_configured_values(self) -> None:
        logger.info("settings_loaded", **self.model_dump())


settings = Settings()
