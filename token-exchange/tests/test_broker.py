"""Unit tests for VaultIdentityBroker."""
import time
from unittest.mock import MagicMock, patch

import jwt
import pytest

from broker.broker import VaultIdentityBroker
from broker.cache import TokenCache
from broker.vault_client import VaultClient
from exceptions.errors import VaultAuthenticationError, VaultTokenGenerationError


def _make_jwt(exp_offset: int = 3600) -> str:
    payload = {"sub": "svc", "exp": int(time.time()) + exp_offset}
    return jwt.encode(payload, "secret", algorithm="HS256")


@pytest.fixture()
def mock_client():
    return MagicMock(spec=VaultClient)


@pytest.fixture()
def fresh_cache():
    return TokenCache(maxsize=10, ttl=60)


class TestGetSignedIdentityToken:
    def test_cache_miss_calls_vault_and_caches(self, mock_client, fresh_cache):
        token = _make_jwt()
        mock_client.generate_signed_id_token.return_value = token

        broker = VaultIdentityBroker(vault_client=mock_client, cache=fresh_cache)
        result = broker.get_signed_identity_token("vault-tok", "my-role")

        assert result.identity_token == token
        assert result.cached is False
        mock_client.generate_signed_id_token.assert_called_once_with("vault-tok", "my-role")

    def test_cache_hit_skips_vault(self, mock_client, fresh_cache):
        token = _make_jwt()
        fresh_cache.set("vault-tok", "my-role", token)

        broker = VaultIdentityBroker(vault_client=mock_client, cache=fresh_cache)
        result = broker.get_signed_identity_token("vault-tok", "my-role")

        assert result.identity_token == token
        assert result.cached is True
        mock_client.generate_signed_id_token.assert_not_called()

    def test_vault_auth_error_propagates(self, mock_client, fresh_cache):
        mock_client.generate_signed_id_token.side_effect = VaultAuthenticationError("forbidden")

        broker = VaultIdentityBroker(vault_client=mock_client, cache=fresh_cache)
        with pytest.raises(VaultAuthenticationError):
            broker.get_signed_identity_token("bad-tok", "my-role")

    def test_vault_generation_error_retries_then_raises(self, mock_client, fresh_cache):
        mock_client.generate_signed_id_token.side_effect = VaultTokenGenerationError("oops")

        broker = VaultIdentityBroker(vault_client=mock_client, cache=fresh_cache)
        with pytest.raises(VaultTokenGenerationError):
            broker.get_signed_identity_token("vault-tok", "my-role")

        # tenacity retries 3 times
        assert mock_client.generate_signed_id_token.call_count == 3

    def test_expires_at_reflects_jwt_exp(self, mock_client, fresh_cache):
        exp = int(time.time()) + 900
        token = jwt.encode({"sub": "svc", "exp": exp}, "secret", algorithm="HS256")
        mock_client.generate_signed_id_token.return_value = token

        broker = VaultIdentityBroker(vault_client=mock_client, cache=fresh_cache)
        result = broker.get_signed_identity_token("vault-tok", "my-role")

        assert result.expires_at == exp
