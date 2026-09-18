"""Unit tests for the KeycloakTokenExchangeClient HTTP layer."""
from unittest.mock import MagicMock, patch

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
