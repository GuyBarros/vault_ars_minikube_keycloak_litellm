"""Unit tests for the KeycloakTokenExchangeClient HTTP layer."""
from unittest.mock import MagicMock, patch

import pytest
import requests

from exceptions.errors import VerifyTokenExchangeError
from keycloak.keycloak_client import KeycloakTokenExchangeClient


def _ok_response() -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.ok = True
    response.json.return_value = {"access_token": "obo-access-token"}
    return response


class TestExchangeOBOToken:
    def test_sends_client_secret_in_form_body(self):
        client = KeycloakTokenExchangeClient(
            base_url="https://keycloak.example.com",
            realm="demo",
            audience="user-mcp",
            client_id="the-client-id",
            client_secret="the-client-secret",
        )

        with patch("keycloak.keycloak_client.requests.post", return_value=_ok_response()) as post:
            client.exchange_obo_token("subject-tok", "actor-tok", "users.read")

        sent = post.call_args.kwargs["data"]
        assert sent["client_id"] == "the-client-id"
        assert sent["client_secret"] == "the-client-secret"
        assert sent["delegation_actor"] == "actor-tok"
        assert sent["audience"] == "user-mcp"

    def test_debug_log_redacts_client_secret(self):
        client = KeycloakTokenExchangeClient(
            base_url="https://keycloak.example.com",
            realm="demo",
            audience="user-mcp",
            client_id="the-client-id",
            client_secret="the-client-secret",
        )

        with patch("keycloak.keycloak_client.requests.post", return_value=_ok_response()), patch(
            "keycloak.keycloak_client.logger"
        ) as mock_logger:
            client.exchange_obo_token("subject-tok", "actor-tok", "users.read")

        logged_payload = mock_logger.debug.call_args.kwargs["payload"]
        assert logged_payload["client_secret"] == "<redacted>"
        assert logged_payload["client_id"] == "the-client-id"


class TestIsTokenActive:
    def _client(self):
        return KeycloakTokenExchangeClient(
            base_url="https://keycloak.example.com", realm="demo", audience="user-mcp",
            client_id="the-client-id", client_secret="the-client-secret",
        )

    def _response(self, body, ok=True, status=200):
        response = MagicMock()
        response.ok, response.status_code = ok, status
        response.json.return_value = body
        return response

    def test_introspects_as_the_exchange_client(self):
        with patch("keycloak.keycloak_client.requests.post", return_value=self._response({"active": True})) as post:
            assert self._client().is_token_active("subject-tok") is True
        assert post.call_args.args[0] == "https://keycloak.example.com/realms/demo/protocol/openid-connect/token/introspect"
        assert post.call_args.kwargs["data"] == {
            "token": "subject-tok", "client_id": "the-client-id", "client_secret": "the-client-secret",
        }

    def test_a_revoked_or_ended_session_token_is_not_active(self):
        with patch("keycloak.keycloak_client.requests.post", return_value=self._response({"active": False})):
            assert self._client().is_token_active("subject-tok") is False

    def test_fails_closed_when_keycloak_cannot_be_asked(self):
        with patch("keycloak.keycloak_client.requests.post", side_effect=requests.exceptions.ConnectionError("down")):
            with pytest.raises(VerifyTokenExchangeError):
                self._client().is_token_active("subject-tok")
        with patch("keycloak.keycloak_client.requests.post", return_value=self._response({}, ok=False, status=500)):
            with pytest.raises(VerifyTokenExchangeError):
                self._client().is_token_active("subject-tok")
