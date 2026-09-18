"""Application configuration loaded from the .env file."""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

from observability import get_logger

_LOGGER = get_logger("config")
_ENV_PATH = Path(__file__).resolve().parent / ".env"
_DOTENV_VALUES = dotenv_values(_ENV_PATH) if _ENV_PATH.exists() else {}
_SENSITIVE_FRAGMENTS = ("secret", "token", "key", "password", "credential")
_CONFIG_LOGGED = False

load_dotenv(dotenv_path=_ENV_PATH if _ENV_PATH.exists() else None)


def _mask_config_value(key: str, value: str | None) -> str:
    """Redact sensitive config values before logging."""
    if value is None or value == "":
        return ""
    if any(fragment in key.lower() for fragment in _SENSITIVE_FRAGMENTS):
        return "<redacted>"
    return value


def log_loaded_configuration() -> None:
    """Log all keys loaded from .env with sensitive values redacted."""
    global _CONFIG_LOGGED
    if _CONFIG_LOGGED:
        return

    if not _DOTENV_VALUES:
        _LOGGER.debug("No .env file found at %s; using process environment only", _ENV_PATH)
        _CONFIG_LOGGED = True
        return

    redacted_config = {
        key: _mask_config_value(key, os.getenv(key, value))
        for key, value in sorted(_DOTENV_VALUES.items())
    }
    _LOGGER.debug("Loaded .env configuration: %s", json.dumps(redacted_config, sort_keys=True))
    _CONFIG_LOGGED = True


IBM_VERIFY_CLIENT_ID = os.environ["IBM_VERIFY_CLIENT_ID"]
IBM_VERIFY_CLIENT_SECRET = os.environ["IBM_VERIFY_CLIENT_SECRET"]
IBM_VERIFY_TENANT_URL = os.environ["IBM_VERIFY_TENANT_URL"].rstrip("/")
IBM_VERIFY_REDIRECT_URI = os.environ["IBM_VERIFY_REDIRECT_URI"]
IBM_VERIFY_SCOPES = os.getenv("IBM_VERIFY_SCOPES", "openid profile email Agent.Invoke")
AI_AGENT_API_URL = os.getenv("AI_AGENT_API_URL", "").rstrip("/")
