"""Local authorization gate for Keycloak OBO token exchange.

Compares the ``groups`` claim embedded in a caller's ``subject_token`` against
the scope set being requested. Raises :class:`VerifyAuthorizationError` (mapped
to HTTP 403 in the API layer) when the caller's groups do not entitle every
requested scope. The check fails closed: missing/malformed claims and unknown
scopes both deny.
"""
import jwt

from exceptions.errors import VerifyAuthorizationError

SCOPE_REQUIREMENTS: dict[str, frozenset[str]] = {
    "users.read": frozenset({"reader", "writer", "admin"}),
    "users.write": frozenset({"writer", "admin"}),
}


def _groups_from_token(token: str) -> list[str] | None:
    """Return the ``groups`` claim from *token* without signature verification.

    Keycloak's group-membership mapper always emits a JSON array of group
    names (``["reader", "writer", "admin"]``), never a scalar.
    """
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    groups = payload.get("groups")
    if isinstance(groups, list) and all(isinstance(g, str) for g in groups):
        return groups or None
    return None


def authorize_scope(subject_token: str, scope: str) -> None:
    """Raise :class:`VerifyAuthorizationError` if *subject_token*'s groups don't entitle *scope*.

    *scope* is the space-separated string from the request. Every scope token
    must be present in :data:`SCOPE_REQUIREMENTS` and at least one of the user's
    groups must satisfy each scope's required-groups set.
    """
    groups = _groups_from_token(subject_token)
    if not groups:
        raise VerifyAuthorizationError(
            "subject_token missing or malformed 'groups' claim"
        )

    user_groups = set(groups)
    requested = [s for s in scope.split() if s]
    for requested_scope in requested:
        required = SCOPE_REQUIREMENTS.get(requested_scope)
        if required is None:
            raise VerifyAuthorizationError(
                f"scope '{requested_scope}' is not permitted by policy"
            )
        if user_groups.isdisjoint(required):
            raise VerifyAuthorizationError(
                f"user groups {sorted(user_groups)} are not authorized for scope "
                f"'{requested_scope}' (requires one of {sorted(required)})"
            )
