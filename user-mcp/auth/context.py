from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any, Optional


current_obo_token: ContextVar[Optional[str]] = ContextVar(
    "current_obo_token", default=None
)
current_obo_scope: ContextVar[Optional[str]] = ContextVar(
    "current_obo_scope", default=None
)
current_obo_user: ContextVar[Optional[str]] = ContextVar(
    "current_obo_user", default=None
)
current_obo_groups: ContextVar[Optional[list[str]]] = ContextVar(
    "current_obo_groups", default=None
)


def bind_request_identity(
    token: str | None,
    scope: str | None,
    user: str | None = None,
    groups: list[str] | None = None,
) -> tuple[Token[Any], Token[Any], Token[Any], Token[Any]]:
    return (
        current_obo_token.set(token),
        current_obo_scope.set(scope),
        current_obo_user.set(user),
        current_obo_groups.set(groups),
    )


def reset_request_identity(
    tokens: tuple[Token[Any], Token[Any], Token[Any], Token[Any]]
) -> None:
    token_reset, scope_reset, user_reset, groups_reset = tokens
    current_obo_groups.reset(groups_reset)
    current_obo_user.reset(user_reset)
    current_obo_scope.reset(scope_reset)
    current_obo_token.reset(token_reset)
