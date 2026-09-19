import json
import logging
from pathlib import Path

from config import Settings, load_settings


def test_load_settings_logs_all_configured_values(monkeypatch, tmp_path, caplog):
    actor_token_path = tmp_path / "actor-token"
    configured_values = {
        "LANGCHAIN_MODEL": "openai:gpt-5.4",
        "ACTOR_TOKEN_PATH": str(actor_token_path),
        "TOKEN_EXCHANGE_URL": "https://example.test/v1/identity/obo-token",
        "TOKEN_EXCHANGE_TIMEOUT_SECONDS": "42.5",
        "OBO_ROLE_NAME": "runtime-role",
        "BYPASS_AUTH_TOKEN_EXCHANGE": "true",
        "HOST": "127.0.0.1",
        "PORT": "9000",
        "LOG_LEVEL": "debug",
        "USER_MCP_URL": "http://user-mcp.local/mcp",
    }

    for key, value in configured_values.items():
        monkeypatch.setenv(key, value)

    with caplog.at_level(logging.DEBUG, logger="config"):
        settings = load_settings()

    assert settings == Settings(
        model="openai:gpt-5.4",
        ollama_base_url=None,
        litellm_base_url=None,
        actor_token_path=Path(str(actor_token_path)),
        token_exchange_url="https://example.test/v1/identity/obo-token",
        token_exchange_timeout_seconds=42.5,
        obo_role_name="runtime-role",
        bypass_auth_token_exchange=True,
        host="127.0.0.1",
        port=9000,
        log_level="DEBUG",
        user_mcp_url="http://user-mcp.local/mcp",
        mcp_tool_call_timeout_seconds=120.0,
    )
    assert len(caplog.records) == 1

    payload = json.loads(caplog.records[0].getMessage())
    assert payload["actor_token_path"] == str(actor_token_path)
    assert payload["event"] == "settings_loaded"
    assert payload["host"] == "127.0.0.1"
    assert payload["log_level"] == "DEBUG"
    assert payload["model"] == "openai:gpt-5.4"
    assert payload["bypass_auth_token_exchange"] is True
    assert payload["obo_role_name"] == "runtime-role"
    assert payload["port"] == 9000
    assert payload["token_exchange_timeout_seconds"] == 42.5
    assert payload["token_exchange_url"] == "https://example.test/v1/identity/obo-token"
    assert payload["user_mcp_url"] == "http://user-mcp.local/mcp"
    assert payload["mcp_tool_call_timeout_seconds"] == 120.0
    assert payload["level"] == "INFO"
    assert payload["logger"] == "config"
    assert payload["hostname"]
    assert "host_ip" in payload
    assert payload["module"] == "config"
    assert payload["function"] == "load_settings"
    assert payload["method_name"] == "load_settings"
    assert isinstance(payload["line_number"], int)
    assert payload["process_id"] > 0
    assert payload["timestamp"]
