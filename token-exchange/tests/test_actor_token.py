"""Unit tests for ActorTokenValidator (Vault identity-token verification)."""
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from exceptions.errors import VerifyAuthenticationError, VerifyTokenExchangeError
from keycloak.actor_token import ActorTokenValidator

ISSUER = "https://vault.example:8200/v1/identity/oidc"
AUDIENCE = "agent-actor"


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


VAULT_KEY = _key()


class FakeJwks:
    """Stands in for PyJWKClient: serves Vault's public key, or fails like an unreachable Vault."""

    def __init__(self, public_key, error: Exception | None = None):
        self._public_key, self._error = public_key, error

    def get_signing_key_from_jwt(self, token):
        if self._error:
            raise self._error
        return SimpleNamespace(key=self._public_key)


def _token(key=VAULT_KEY, **overrides):
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": "entity-1", "agent_id": "ai-agent", "iat": now, "exp": now + 3600}
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "vault-key"})


def _validator(**kwargs):
    return ActorTokenValidator(ISSUER, AUDIENCE, jwks_client=kwargs.pop("jwks", FakeJwks(VAULT_KEY.public_key())), **kwargs)


def test_accepts_a_token_signed_by_vault():
    claims = _validator().validate(_token())
    assert claims["agent_id"] == "ai-agent"


def test_rejects_a_token_signed_with_another_key():
    with pytest.raises(VerifyAuthenticationError):
        _validator().validate(_token(key=_key()))


def test_rejects_an_expired_token():
    with pytest.raises(VerifyAuthenticationError):
        _validator().validate(_token(exp=int(time.time()) - 60))


def test_rejects_the_wrong_issuer():
    with pytest.raises(VerifyAuthenticationError):
        _validator().validate(_token(iss="https://other.example/oidc"))


def test_rejects_the_wrong_audience():
    with pytest.raises(VerifyAuthenticationError):
        _validator().validate(_token(aud="some-other-role"))


@pytest.mark.parametrize("claim", ["exp", "iat", "iss", "aud"])
def test_rejects_a_token_missing_a_required_claim(claim):
    with pytest.raises(VerifyAuthenticationError):
        _validator().validate(_token(**{claim: None}))


def test_rejects_an_unsigned_token():
    unsigned = jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "iat": 1, "exp": int(time.time()) + 60, "agent_id": "x"}, None, algorithm="none")
    with pytest.raises(VerifyAuthenticationError):
        _validator().validate(unsigned)


def test_rejects_garbage():
    with pytest.raises(VerifyAuthenticationError):
        _validator(jwks=FakeJwks(None, error=jwt.DecodeError("Not enough segments"))).validate("forged-actor-token")


def test_fails_closed_when_vaults_keys_are_unreachable():
    down = FakeJwks(None, error=jwt.PyJWKClientConnectionError("connection refused"))
    with pytest.raises(VerifyTokenExchangeError):
        _validator(jwks=down).validate(_token())


@pytest.mark.parametrize("issuer, audience", [("", AUDIENCE), (ISSUER, ""), ("", "")])
def test_fails_closed_when_not_configured(issuer, audience):
    validator = ActorTokenValidator(issuer, audience, jwks_client=FakeJwks(VAULT_KEY.public_key()))
    with pytest.raises(VerifyTokenExchangeError, match="not configured"):
        validator.validate(_token())
