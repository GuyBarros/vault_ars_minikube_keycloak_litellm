from __future__ import annotations

from unittest.mock import AsyncMock

import jwt
import pytest
from fastmcp import Client, FastMCP

import storage.postgres_repo as postgres_repo_module
from auth.context import bind_request_identity, reset_request_identity
from storage.postgres_repo import PostgresUserRepository
from tools.users import register_tools
from vault_client import DynamicDbCredentials

# _jwt_for_vault always calls decode_unverified() on the OBO token now (no
# more local/CIBA branch), so tests need a real 3-segment JWT rather than a
# bare placeholder string.
_FAKE_JWT = jwt.encode({"sub": "test"}, "test-secret", algorithm="HS256")


class FakeVaultClient:
    async def login_with_jwt(self, jwt_token, role):
        return "parent-vault-token"

    async def read_database_creds(self, client_token, creds_path):
        return DynamicDbCredentials(
            username="v-user", password="v-pass", lease_id="lease-1", lease_duration=60
        )

    async def revoke_lease(self, client_token, lease_id):
        return None

    async def transform_encode(self, client_token, role_name, transformation, value):
        return f"MASKED({transformation})"


def _row(email="a@example.com"):
    return {
        "email": email,
        "first_name": "A",
        "last_name": None,
        "ssn": None,
        "phone": None,
        "credit_card_number": None,
        "ip_address": None,
    }


class FakeConnection:
    def __init__(self, rows):
        self._rows = rows

    async def fetch(self, *args, **kwargs):
        return self._rows

    async def fetchrow(self, *args, **kwargs):
        return self._rows[0]

    async def close(self):
        return None


def _make_repo(monkeypatch, *, rows):
    monkeypatch.setattr(
        postgres_repo_module.asyncpg, "connect", AsyncMock(return_value=FakeConnection(rows))
    )
    return PostgresUserRepository(
        pg_url="postgresql://example/db",
        auth_mode="vault",
        vault_client=FakeVaultClient(),
        vault_jwt_read_role="user-mcp-oidc-read",
        vault_jwt_write_role="user-mcp-oidc-write",
        vault_db_read_path="database/creds/user-mcp-read-role",
        vault_db_write_path="database/creds/user-mcp-write-role",
    )


async def test_read_tool_result_carries_baseline_assurance(monkeypatch):
    repo = _make_repo(monkeypatch, rows=[_row()])
    mcp = FastMCP(name="assurance-test")
    register_tools(mcp, repo)
    tokens = bind_request_identity(token=_FAKE_JWT, scope="users.read", user="user", groups=["reader"])
    try:
        async with Client(mcp) as client:
            result = await client.call_tool("list_all_users", {})
    finally:
        reset_request_identity(tokens)

    assert result.meta["assurance"] == {
        "tool": "list_all_users",
        "decision": "ALLOW",
        "current_loa": 1,
        "required_loa": 1,
    }
    assert result.structured_content["result"][0]["email"] == "a@example.com"


async def test_write_tool_carries_elevated_assurance_from_pep_headers(monkeypatch):
    from auth.context import bind_pep_assurance, reset_pep_assurance

    repo = _make_repo(monkeypatch, rows=[_row()])
    mcp = FastMCP(name="assurance-runtime")
    register_tools(mcp, repo)
    tokens = bind_request_identity(token=_FAKE_JWT, scope="users.write", user="admin", groups=["admin"])
    pep = bind_pep_assurance(
        {"decision": "ALLOW", "current_loa": 2, "required_loa": 2}
    )
    try:
        async with Client(mcp) as client:
            result = await client.call_tool(
                "create_user",
                {"user": {"email": "a@example.com", "first_name": "A", "last_name": "B"}},
            )
    finally:
        reset_pep_assurance(pep)
        reset_request_identity(tokens)

    assert result.meta["assurance"] == {
        "tool": "create_user",
        "decision": "ALLOW",
        "current_loa": 2,
        "required_loa": 2,
    }
