from __future__ import annotations

import json

import pytest
from fastmcp import Client, FastMCP

from auth.context import bind_request_identity, reset_request_identity
from storage.file_repo import FileUserRepository
from tools.users import register_tools


@pytest.fixture
async def mcp_with_repo(users_file):
    repo = FileUserRepository(file_path=users_file)
    await repo.startup()
    mcp = FastMCP(name="user-mcp-test")
    register_tools(mcp, repo)
    # In real requests, JwtAuthMiddleware binds the OBO scope from the JWT.
    # The in-memory FastMCP Client used by these tests bypasses that middleware,
    # so we bind a full-scope identity here to mirror an authenticated caller.
    tokens = bind_request_identity(token=None, scope="users.read users.write")
    try:
        yield mcp
    finally:
        reset_request_identity(tokens)


@pytest.fixture
async def mcp_read_only(users_file):
    """Same MCP server, but the caller's OBO grants only `users.read`."""
    repo = FileUserRepository(file_path=users_file)
    await repo.startup()
    mcp = FastMCP(name="user-mcp-test-readonly")
    register_tools(mcp, repo)
    tokens = bind_request_identity(token=None, scope="users.read")
    try:
        yield mcp
    finally:
        reset_request_identity(tokens)


def _record_payload(call_result) -> dict:
    """FastMCP returns CallToolResult.content as a list of TextContent. The
    structured JSON payload is serialized in the first text block."""
    content = call_result.content[0].text if call_result.content else None
    return json.loads(content) if content else {}


async def test_list_all_users_via_mcp_client(mcp_with_repo):
    async with Client(mcp_with_repo) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools}
        assert names == {
            "list_all_users",
            "search_users_by_first_name",
            "create_user",
            "delete_user_by_email",
            "update_user_by_email",
        }

        result = await client.call_tool("list_all_users", {})
        assert result.is_error is False


async def test_tools_advertise_required_scopes_via_meta(mcp_with_repo):
    """Each tool's _meta.required_scopes is the contract the agent reads to
    pick the right OBO scope before calling it."""
    expected = {
        "list_all_users": ["users.read"],
        "search_users_by_first_name": ["users.read"],
        "create_user": ["users.write"],
        "delete_user_by_email": ["users.write"],
        "update_user_by_email": ["users.write"],
    }
    async with Client(mcp_with_repo) as client:
        tools = await client.list_tools()
        for tool in tools:
            assert tool.meta is not None, f"{tool.name} missing _meta"
            assert tool.meta.get("required_scopes") == expected[tool.name], (
                f"{tool.name} advertised wrong scopes: {tool.meta}"
            )


async def test_search_users_by_first_name_via_mcp_client(mcp_with_repo):
    async with Client(mcp_with_repo) as client:
        result = await client.call_tool(
            "search_users_by_first_name", {"first_name": "Noah"}
        )
        assert result.is_error is False


async def test_create_user_via_mcp_client(mcp_with_repo):
    async with Client(mcp_with_repo) as client:
        result = await client.call_tool(
            "create_user",
            {"user": {"email": "ava.brown@example.com", "first_name": "Ava", "last_name": "Brown"}},
        )
        assert result.is_error is False
        assert _record_payload(result)["email"] == "ava.brown@example.com"

        listing = _record_payload(await client.call_tool("list_all_users", {}))
        emails = {u["email"] for u in listing}
        assert "ava.brown@example.com" in emails


async def test_create_user_requires_first_and_last_name(mcp_with_repo):
    """email/first_name/last_name are mandatory; ssn/phone/credit_card_number/
    ip_address stay optional (see models.NewUserRecord)."""
    async with Client(mcp_with_repo) as client:
        with pytest.raises(Exception, match="last_name"):
            await client.call_tool(
                "create_user",
                {"user": {"email": "noname@example.com", "first_name": "No"}},
            )
        with pytest.raises(Exception, match="first_name"):
            await client.call_tool(
                "create_user",
                {"user": {"email": "noname2@example.com", "last_name": "Name"}},
            )

        result = await client.call_tool(
            "create_user",
            {"user": {"email": "minimal@example.com", "first_name": "Min", "last_name": "Imal"}},
        )
        assert result.is_error is False


async def test_create_user_duplicate_returns_error(mcp_with_repo):
    async with Client(mcp_with_repo) as client:
        # emma.taylor@example.com is already present in the seed data.
        with pytest.raises(Exception, match="already exists"):
            await client.call_tool(
                "create_user",
                {"user": {"email": "emma.taylor@example.com", "first_name": "Other", "last_name": "Other"}},
            )


async def test_delete_user_via_mcp_client(mcp_with_repo):
    async with Client(mcp_with_repo) as client:
        result = await client.call_tool(
            "delete_user_by_email", {"email": "emma.taylor@example.com"}
        )
        assert result.is_error is False

        with pytest.raises(Exception, match="not found"):
            await client.call_tool(
                "delete_user_by_email", {"email": "ghost@example.com"}
            )


async def test_update_user_via_mcp_client(mcp_with_repo):
    async with Client(mcp_with_repo) as client:
        result = await client.call_tool(
            "update_user_by_email",
            {
                "email": "noah.thompson206@example.com",
                "user": {
                    "email": "noah.updated@example.com",
                    "first_name": "Noah",
                    "phone": "+1-000-000-0000",
                },
            },
        )
        assert result.is_error is False


async def test_read_only_caller_can_list(mcp_read_only):
    async with Client(mcp_read_only) as client:
        result = await client.call_tool("list_all_users", {})
        assert result.is_error is False


async def test_read_only_caller_blocked_from_create(mcp_read_only):
    async with Client(mcp_read_only) as client:
        with pytest.raises(Exception, match="users.write"):
            await client.call_tool(
                "create_user",
                {"user": {"email": "blocked@example.com", "first_name": "Blocked", "last_name": "Blocked"}},
            )


async def test_read_only_caller_blocked_from_delete(mcp_read_only):
    async with Client(mcp_read_only) as client:
        with pytest.raises(Exception, match="users.write"):
            await client.call_tool(
                "delete_user_by_email", {"email": "emma.taylor@example.com"}
            )


async def test_read_only_caller_blocked_from_update(mcp_read_only):
    async with Client(mcp_read_only) as client:
        with pytest.raises(Exception, match="users.write"):
            await client.call_tool(
                "update_user_by_email",
                {
                    "email": "noah.thompson206@example.com",
                    "user": {
                        "email": "noah.thompson206@example.com",
                        "first_name": "Noah",
                    },
                },
            )
