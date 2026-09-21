import requests

from config.settings import settings
from exceptions.errors import (
    VerifyAuthenticationError,
    VerifyTokenExchangeError,
)
from app_logging.logger import get_logger

logger = get_logger(__name__)

_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:token-exchange"
_REQUESTED_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"
_SUBJECT_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"


class KeycloakTokenExchangeClient:
    """HTTP client for Keycloak on-behalf-of (OBO) token exchange.

    Performs an RFC 8693 token exchange against the Keycloak realm token
    endpoint. Configuration is read from :mod:`config.settings` at
    instantiation time.
    """

    def __init__(
        self,
        base_url: str | None = None,
        realm: str | None = None,
        audience: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        base = (base_url or settings.keycloak_url).rstrip("/")
        self._token_url = (
            f"{base}/realms/{realm or settings.keycloak_realm}"
            "/protocol/openid-connect/token"
        )
        self._audience = audience or settings.keycloak_token_exchange_audience
        self._client_id = client_id or settings.obo_client_id
        self._client_secret = client_secret or settings.obo_client_secret

    def exchange_obo_token(
        self, subject_token: str, actor_token: str, scope: str
    ) -> dict:
        """Exchange *subject_token* + *actor_token* for a Keycloak access token.

        Args:
            subject_token: The caller's access token (JWT) to act on behalf of.
            actor_token:   The Vault Identity JWT that identifies the acting
                            service. Keycloak cannot itself verify a
                            Vault-issued token as a first-class RFC 8693
                            ``actor_token``, so it travels as the custom
                            ``delegation_actor`` form field instead — the
                            keycloak-providers SPI (attached to this client's
                            protocol mappers) decodes it and stamps an ``act``
                            claim into the issued token.
            scope:         Space-separated OAuth scopes to request on the OBO token.

        Returns:
            Parsed JSON response dict from Keycloak (contains ``access_token``,
            ``token_type``, ``expires_in``, etc.).

        Raises:
            VerifyAuthenticationError:  Keycloak returned 401.
            VerifyTokenExchangeError:   Any other HTTP or connection failure.
        """

        payload = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "grant_type": _GRANT_TYPE,
            "requested_token_type": _REQUESTED_TOKEN_TYPE,
            "subject_token_type": _SUBJECT_TOKEN_TYPE,
            "subject_token": subject_token,
            "audience": self._audience,
            "delegation_actor": actor_token,
            "scope": scope,
        }

        logger.debug(
            "keycloak_obo_token_exchange_payload",
            payload={
                k: (
                    v
                    if k not in {"subject_token", "delegation_actor", "client_secret"}
                    else "<redacted>"
                )
                for k, v in payload.items()
            },
        )

        try:
            response = requests.post(
                self._token_url,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10,
            )
        except requests.exceptions.RequestException as exc:
            raise VerifyTokenExchangeError(
                f"Network error contacting Keycloak: {exc}"
            ) from exc

        if response.status_code == 401:
            raise VerifyAuthenticationError(
                "Keycloak rejected the OBO request: unauthorized"
            )

        if not response.ok:
            logger.warning(
                "keycloak_obo_http_error",
                status_code=response.status_code,
                response_body=response.text,
            )
            raise VerifyTokenExchangeError(
                f"Keycloak OBO exchange failed with HTTP {response.status_code}"
            )

        try:
            return response.json()
        except Exception as exc:
            raise VerifyTokenExchangeError(
                "Keycloak returned a non-JSON response"
            ) from exc

    def is_token_active(self, token: str) -> bool:
        """Whether Keycloak still considers *token* active: not revoked and its
        session not ended (introspection, RFC 7662). A JWT's own signature and
        expiry say nothing about either, which is why this asks Keycloak.

        Introspection is allowed for tokens whose audience includes this client,
        which is true of the user's login token (the subject token).

        Raises:
            VerifyTokenExchangeError: Keycloak couldn't be asked (fail closed).
        """
        try:
            response = requests.post(
                f"{self._token_url}/introspect",
                data={"token": token, "client_id": self._client_id, "client_secret": self._client_secret},
                timeout=10,
            )
        except requests.exceptions.RequestException as exc:
            raise VerifyTokenExchangeError(f"Network error contacting Keycloak: {exc}") from exc
        if not response.ok:
            raise VerifyTokenExchangeError(f"Keycloak token introspection failed with HTTP {response.status_code}")
        try:
            return bool(response.json().get("active"))
        except Exception as exc:
            raise VerifyTokenExchangeError("Keycloak returned a non-JSON response") from exc
