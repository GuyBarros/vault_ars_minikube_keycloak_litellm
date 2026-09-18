from app_logging.logger import get_logger
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


logger = get_logger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="IDENTITY_BROKER_",
        case_sensitive=False,
        env_file=".env",
        extra="ignore",
    )

    vault_addr: str = "https://127.0.0.1:8200"
    vault_tls_verify: bool = True
    # Path to a PEM CA bundle for Vault's self-signed or private CA certificate.
    # When set, TLS verification uses this bundle instead of the default certifi roots.
    vault_ca_bundle: str | None = None

    # Keycloak OBO token exchange settings.
    keycloak_url: str = ""
    keycloak_realm: str = "demo"
    keycloak_token_exchange_audience: str = "user-mcp"
    obo_client_id: str = ""
    # Confidential client secret for the token-exchange client. Required: the
    # broker fails fast at startup if it is missing or empty (Keycloak
    # authenticates the token-exchange grant with client_secret_post —
    # client_id + client_secret in the request body).
    obo_client_secret: str = Field(min_length=1)

    cache_ttl: int = 3600       # seconds; also the TTLCache eviction window
    cache_maxsize: int = 1024   # max number of cached tokens

    log_level: str = "INFO"

    def log_configured_values(self) -> None:
        values = self.model_dump()
        if values.get("obo_client_secret"):
            values["obo_client_secret"] = "***"
        logger.info("settings_loaded", **values)


settings = Settings()
