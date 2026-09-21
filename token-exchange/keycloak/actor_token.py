"""Validation of the Vault-signed actor token (the agent's identity).

The actor token is a Vault OIDC identity token. Keycloak can't verify it, so
this service does before forwarding it: signature against the keys Vault
publishes, plus issuer, audience and expiry. Without this, anyone who can reach
the exchange endpoint could name any agent_id.
"""
import ssl

import jwt

from config.settings import settings
from exceptions.errors import VerifyAuthenticationError, VerifyTokenExchangeError


class ActorTokenValidator:
    def __init__(self, issuer: str, audience: str, jwks_client=None, leeway_seconds: int = 10) -> None:
        self._issuer = issuer
        self._audience = audience
        self._jwks_client = jwks_client
        self._leeway = leeway_seconds

    @classmethod
    def from_settings(cls) -> "ActorTokenValidator":
        return cls(settings.actor_issuer, settings.actor_audience)

    def _client(self):
        if self._jwks_client is None:
            if settings.vault_ca_bundle:
                context = ssl.create_default_context(cafile=settings.vault_ca_bundle)
            elif not settings.vault_tls_verify:
                context = ssl._create_unverified_context()
            else:
                context = ssl.create_default_context()
            self._jwks_client = jwt.PyJWKClient(
                f"{self._issuer.rstrip('/')}/.well-known/keys",
                cache_keys=True,
                lifespan=300,
                timeout=10,
                ssl_context=context,
            )
        return self._jwks_client

    def validate(self, actor_token: str) -> dict:
        """Return the verified claims of *actor_token*.

        Raises:
            VerifyAuthenticationError: the token is not a valid, current Vault identity token.
            VerifyTokenExchangeError:  validation is unconfigured, or Vault's keys are unreachable
                                       (fail closed: the exchange is refused either way).
        """
        if not self._issuer or not self._audience:
            raise VerifyTokenExchangeError(
                "actor token validation is not configured "
                "(set IDENTITY_BROKER_ACTOR_ISSUER and IDENTITY_BROKER_ACTOR_AUDIENCE)"
            )
        try:
            key = self._client().get_signing_key_from_jwt(actor_token).key
            return jwt.decode(
                actor_token,
                key,
                algorithms=["RS256"],
                issuer=self._issuer,
                audience=self._audience,
                options={"require": ["exp", "iat", "iss", "aud"]},
                leeway=self._leeway,
            )
        except jwt.PyJWKClientConnectionError as exc:
            raise VerifyTokenExchangeError(f"Vault signing keys are unavailable: {exc}") from exc
        except jwt.PyJWTError as exc:
            raise VerifyAuthenticationError(f"actor_token is not a valid Vault identity token: {exc}") from exc
