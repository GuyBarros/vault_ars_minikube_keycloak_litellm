class VaultBrokerError(Exception):
    """Base exception for all Vault identity broker errors."""


class VaultAuthenticationError(VaultBrokerError):
    """Raised when the supplied Vault token is invalid or lacks permission."""


class VaultTokenGenerationError(VaultBrokerError):
    """Raised when Vault fails to generate a signed identity token."""


class CacheError(VaultBrokerError):
    """Raised on unexpected cache read/write failures."""


class VerifyOBOError(Exception):
    """Base exception for Keycloak OBO token exchange errors."""


class VerifyAuthenticationError(VerifyOBOError):
    """Raised when Keycloak rejects the OBO request due to invalid credentials."""


class VerifyTokenExchangeError(VerifyOBOError):
    """Raised when Keycloak fails to complete the OBO token exchange."""


class VerifyAuthorizationError(VerifyOBOError):
    """Raised when the subject_token's groups don't entitle the requested scope."""
