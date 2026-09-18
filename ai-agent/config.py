from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from dotenv import load_dotenv
from logging_utils import configure_logging, log_event

load_dotenv()
LOGGER = logging.getLogger("config")


@dataclass
class Settings:
    model: str
    ollama_base_url: str | None
    litellm_base_url: str | None
    litellm_api_key: str | None
    actor_token_path: Path
    token_exchange_url: str
    token_exchange_timeout_seconds: float
    obo_role_name: str
    bypass_auth_token_exchange: bool
    host: str
    port: int
    log_level: str
    user_mcp_url: str
    mcp_tool_call_timeout_seconds: float


def load_settings() -> Settings:
    settings = Settings(
        model=_load_model(),
        # Only consulted for LANGCHAIN_MODEL="ollama:...": Ollama's default
        # (localhost:11434) is never right when the agent runs in a
        # container, since Ollama itself runs elsewhere.
        ollama_base_url=os.getenv("OLLAMA_BASE_URL"),
        # When set, routes every model call through the LiteLLM gateway's
        # OpenAI-compatible endpoint instead of talking to a provider
        # directly - LANGCHAIN_MODEL then names one of the gateway's own
        # model_list aliases (see litellm-gateway/config.yaml), not a raw
        # provider:model string. Takes priority over the ollama_base_url
        # branch below when both are set.
        litellm_base_url=os.getenv("LITELLM_BASE_URL"),
        litellm_api_key=os.getenv("LITELLM_API_KEY"),
        actor_token_path=Path(
            os.getenv("ACTOR_TOKEN_PATH", "/vault/secrets/actor-token")
        ),
        token_exchange_url=os.getenv(
            "TOKEN_EXCHANGE_URL", "http://localhost:8080/v1/identity/obo-token"
        ),
        token_exchange_timeout_seconds=float(
            os.getenv("TOKEN_EXCHANGE_TIMEOUT_SECONDS", "10")
        ),
        obo_role_name=os.getenv("OBO_ROLE_NAME", "agent-runtime"),
        bypass_auth_token_exchange=_load_bool(
            "BYPASS_AUTH_TOKEN_EXCHANGE", default=False
        ),
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        # Full URL of the MCP endpoint the agent discovers tools from.
        # In cluster this is LiteLLM's MCP gateway (`/user_mcp/mcp`), not
        # user-mcp directly — LiteLLM is the PEP.
        user_mcp_url=os.getenv("USER_MCP_URL", "http://localhost:8090/mcp"),
        # Must comfortably exceed user-mcp's CIBA poll wait
        # (USER_MCP_CIBA_POLL_TIMEOUT_SECONDS, default 110s) — create_user
        # and delete_user_by_email block the MCP tool call until the human
        # approves. The library's own default (30s) would cut it off early.
        mcp_tool_call_timeout_seconds=float(
            os.getenv("MCP_TOOL_CALL_TIMEOUT_SECONDS", "120")
        ),
    )
    configure_logging(settings.log_level)
    serialized_settings = asdict(settings)
    serialized_settings["actor_token_path"] = str(settings.actor_token_path)
    log_event(LOGGER, "settings_loaded", message="Settings loaded", **serialized_settings)
    return settings


def _load_model() -> str:
    configured_model = os.getenv("LANGCHAIN_MODEL")
    if configured_model:
        return configured_model

    return "openai:gpt-5-mini"


def _load_bool(env_name: str, default: bool) -> bool:
    configured_value = os.getenv(env_name)
    if configured_value is None:
        return default

    normalized_value = configured_value.strip().lower()
    if normalized_value in {"1", "true", "yes", "on"}:
        return True
    if normalized_value in {"0", "false", "no", "off"}:
        return False

    raise ValueError(
        f"{env_name} must be one of: 1, true, yes, on, 0, false, no, off."
    )
