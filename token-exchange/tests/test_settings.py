"""Tests for settings loading from .env and environment variables."""

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from config.settings import Settings


class TestSettings:
    def test_reads_values_from_dotenv_file(self, tmp_path, monkeypatch):
        dotenv_path = tmp_path / ".env"
        dotenv_path.write_text(
            "IDENTITY_BROKER_KEYCLOAK_URL=https://keycloak.example.com\n"
            "IDENTITY_BROKER_OBO_CLIENT_ID=dotenv-client-id\n"
            "IDENTITY_BROKER_OBO_CLIENT_SECRET=dotenv-client-secret\n"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("IDENTITY_BROKER_OBO_CLIENT_SECRET", raising=False)

        settings = Settings()

        assert settings.keycloak_url == "https://keycloak.example.com"
        assert settings.obo_client_id == "dotenv-client-id"
        assert settings.obo_client_secret == "dotenv-client-secret"

    def test_environment_variables_override_dotenv_values(self, tmp_path, monkeypatch):
        dotenv_path = tmp_path / ".env"
        dotenv_path.write_text(
            "IDENTITY_BROKER_KEYCLOAK_URL=https://dotenv.example.com\n"
            "IDENTITY_BROKER_OBO_CLIENT_ID=dotenv-client-id\n"
            "IDENTITY_BROKER_OBO_CLIENT_SECRET=dotenv-client-secret\n"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv(
            "IDENTITY_BROKER_KEYCLOAK_URL",
            "https://env.example.com",
        )

        settings = Settings()

        assert settings.keycloak_url == "https://env.example.com"
        assert settings.obo_client_id == "dotenv-client-id"

    def test_missing_client_secret_raises(self, tmp_path, monkeypatch):
        dotenv_path = tmp_path / ".env"
        dotenv_path.write_text(
            "IDENTITY_BROKER_KEYCLOAK_URL=https://keycloak.example.com\n"
            "IDENTITY_BROKER_OBO_CLIENT_ID=dotenv-client-id\n"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("IDENTITY_BROKER_OBO_CLIENT_SECRET", raising=False)

        with pytest.raises(ValidationError):
            Settings()

    def test_log_configured_values_logs_all_fields(self):
        settings = Settings(
            vault_addr="https://vault.example.com",
            vault_tls_verify=False,
            vault_ca_bundle="/tmp/vault-ca.pem",
            keycloak_url="https://keycloak.example.com",
            obo_client_id="client-id",
            obo_client_secret="super-secret",
            cache_ttl=120,
            cache_maxsize=50,
            log_level="DEBUG",
        )

        with patch("config.settings.logger") as mock_logger:
            settings.log_configured_values()

        mock_logger.info.assert_called_once_with(
            "settings_loaded",
            vault_addr="https://vault.example.com",
            vault_tls_verify=False,
            vault_ca_bundle="/tmp/vault-ca.pem",
            actor_issuer="",
            actor_audience="",
            keycloak_url="https://keycloak.example.com",
            keycloak_realm="demo",
            keycloak_token_exchange_audience="user-mcp",
            obo_client_id="client-id",
            obo_client_secret="***",
            cache_ttl=120,
            cache_maxsize=50,
            log_level="DEBUG",
        )
