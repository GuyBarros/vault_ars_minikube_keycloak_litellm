from __future__ import annotations

import pytest

from auth.context import bind_request_identity, reset_request_identity
from auth.scope_check import (
    InsufficientScopeError,
    configure_bypass,
    register_tool_scopes,
    require_scopes,
)


@pytest.fixture(autouse=True)
def _ensure_no_bypass():
    configure_bypass(False)
    yield
    configure_bypass(False)


@pytest.fixture
def with_scope():
    def _bind(scope: str | None):
        return bind_request_identity(token=None, scope=scope)

    tokens = []

    def _factory(scope: str | None):
        t = _bind(scope)
        tokens.append(t)
        return t

    yield _factory
    for t in tokens:
        reset_request_identity(t)


def test_require_scopes_passes_when_granted_superset(with_scope):
    register_tool_scopes("toolA", ["users.read"])
    with_scope("users.read users.write")
    require_scopes("toolA")  # no raise


def test_require_scopes_passes_with_exact_match(with_scope):
    register_tool_scopes("toolA", ["users.read"])
    with_scope("users.read")
    require_scopes("toolA")


def test_require_scopes_raises_when_missing(with_scope):
    register_tool_scopes("toolWrite", ["users.write"])
    with_scope("users.read")
    with pytest.raises(InsufficientScopeError) as exc_info:
        require_scopes("toolWrite")
    assert exc_info.value.status_code == 403
    assert exc_info.value.error == "insufficient_scope"
    assert "users.write" in exc_info.value.message


def test_require_scopes_raises_when_no_scope_bound(with_scope):
    register_tool_scopes("toolA", ["users.read"])
    with_scope(None)
    with pytest.raises(InsufficientScopeError):
        require_scopes("toolA")


def test_undeclared_tool_is_denied_by_default():
    # Intentionally do NOT register "ghost_tool".
    with pytest.raises(InsufficientScopeError) as exc_info:
        require_scopes("ghost_tool_never_registered")
    assert "<undeclared>" in exc_info.value.message


def test_bypass_skips_enforcement(with_scope):
    register_tool_scopes("toolWrite", ["users.write"])
    with_scope("")
    configure_bypass(True)
    try:
        require_scopes("toolWrite")  # should not raise
    finally:
        configure_bypass(False)
