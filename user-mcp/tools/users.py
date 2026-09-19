from __future__ import annotations

import logging

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import ToolResult

from auth.scope_check import get_required_scopes, register_tool_scopes, require_scopes
from errors import AppError
from logging_utils import log_event
from models import NewUserRecord, UserRecord
from storage.base import UserRepository

LOGGER = logging.getLogger("user_mcp.tools.users")

# Single source of truth for the tool → scope contract. Surfaced both as MCP
# tool `_meta.required_scopes` (so clients can read it via tools/list) and
# registered with the in-process scope_check registry (so the dispatcher can
# enforce it on every call).
TOOL_SCOPE_REQUIREMENTS: dict[str, list[str]] = {
    "list_all_users": ["users.read"],
    "search_users_by_first_name": ["users.read"],
    "create_user": ["users.write"],
    "delete_user_by_email": ["users.write"],
    "update_user_by_email": ["users.write"],
}


def _meta_for(tool_name: str) -> dict[str, list[str]]:
    return {"required_scopes": list(TOOL_SCOPE_REQUIREMENTS[tool_name])}


def register_tools(mcp: FastMCP, repo: UserRepository) -> None:
    """Attach the user-management tools to the given FastMCP server.

    Tools are async closures over `repo`. AppError raised by the repository
    is translated into FastMCP `ToolError` so the model-facing error message
    preserves the validation-style intent (already-exists, not-found, etc.).

    Each tool declares its required OBO scopes via `meta.required_scopes`,
    which FastMCP surfaces under the tool's `_meta` field in `tools/list`.
    The same scopes are registered with `scope_check` so `_run_tool` can
    enforce them on every invocation (defense in depth — agents must pass a
    correctly-scoped OBO token, and this server independently verifies it).

    Writes may additionally block on a human-in-the-loop CIBA approval.
    In USER_MCP_PEP_MODE=local that gate lives in storage/postgres_repo.py
    (driven by a ciba/<action>/<user> Vault ACL policy switch). In runtime
    mode LiteLLM already completed CIBA and this dispatcher only executes.
    """

    for tool_name, scopes in TOOL_SCOPE_REQUIREMENTS.items():
        register_tool_scopes(tool_name, scopes)

    log_event(
        LOGGER,
        "tool_scope_contract_registered",
        message=(
            "Registered tool→scope contract: "
            + ", ".join(
                f"{name}={sorted(scopes)}"
                for name, scopes in TOOL_SCOPE_REQUIREMENTS.items()
            )
        ),
        tool_scopes={name: sorted(scopes) for name, scopes in TOOL_SCOPE_REQUIREMENTS.items()},
    )

    @mcp.tool(
        name="list_all_users",
        description=(
            "Return every user currently stored in the user repository. "
            "Use this whenever the caller asks to list, show, or inspect all users."
        ),
        meta=_meta_for("list_all_users"),
    )
    async def list_all_users() -> list[UserRecord]:
        return await _run_tool(
            "list_all_users",
            lambda: repo.list_all(),
            result_summary=lambda result: {"count": len(result)},
            repo=repo,
        )

    @mcp.tool(
        name="search_users_by_first_name",
        description=(
            "Return users whose first name matches the provided value (case-insensitive, exact). "
            "Use this whenever the caller asks to find or look up users by first name."
        ),
        meta=_meta_for("search_users_by_first_name"),
    )
    async def search_users_by_first_name(first_name: str) -> list[UserRecord]:
        return await _run_tool(
            "search_users_by_first_name",
            lambda: repo.search_by_first_name(first_name),
            result_summary=lambda result: {
                "count": len(result),
                "first_name": first_name,
            },
            repo=repo,
        )

    @mcp.tool(
        name="create_user",
        description=(
            "Create a new user. Use this when the caller asks to add a user. "
            "Email must be unique. This may pause while the caller approves the "
            "request on their device (a CIBA push) before the user is created."
        ),
        meta=_meta_for("create_user"),
    )
    async def create_user(user: NewUserRecord) -> UserRecord:
        return await _run_tool(
            "create_user",
            lambda: repo.create(UserRecord(**user.model_dump())),
            result_summary=lambda result: {"email": result.email},
            repo=repo,
        )

    @mcp.tool(
        name="delete_user_by_email",
        description=(
            "Delete a user by email and return the deleted record. "
            "Use this when the caller asks to remove or delete a user by email. "
            "This may pause while the caller approves the request on their device "
            "(a CIBA push) before the user is deleted."
        ),
        meta=_meta_for("delete_user_by_email"),
    )
    async def delete_user_by_email(email: str) -> UserRecord:
        return await _run_tool(
            "delete_user_by_email",
            lambda: repo.delete_by_email(email),
            result_summary=lambda result: {"email": result.email},
            repo=repo,
        )

    @mcp.tool(
        name="update_user_by_email",
        description=(
            "Update a user identified by email and return the updated record. "
            "Use this when the caller asks to modify a user by email."
        ),
        meta=_meta_for("update_user_by_email"),
    )
    async def update_user_by_email(email: str, user: UserRecord) -> UserRecord:
        return await _run_tool(
            "update_user_by_email",
            lambda: repo.update_by_email(email, user),
            result_summary=lambda result: {"email": result.email},
            repo=repo,
        )


async def _run_tool(tool_name, action, result_summary, repo=None):
    try:
        require_scopes(tool_name)
        result = await action()
    except AppError as exc:
        log_event(
            LOGGER,
            "tool_failed",
            level=logging.ERROR,
            message=f"{tool_name} failed: {exc.message}",
            tool=tool_name,
            error=exc.error,
            status_code=exc.status_code,
        )
        raise ToolError(exc.message) from exc
    except Exception as exc:  # pragma: no cover - defensive guard
        log_event(
            LOGGER,
            "tool_failed",
            level=logging.ERROR,
            message=f"{tool_name} failed unexpectedly: {exc}",
            tool=tool_name,
            error="agent_error",
            status_code=500,
        )
        raise ToolError(f"{tool_name} failed: {exc}") from exc

    summary = result_summary(result) if result_summary else {}
    log_event(
        LOGGER,
        "tool_invoked",
        message=f"{tool_name} invoked",
        tool=tool_name,
        required_scopes=sorted(get_required_scopes(tool_name)),
        **summary,
    )

    assurance = repo.get_last_assurance() if repo is not None else None
    if assurance is None:
        return result
    return ToolResult(
        content=result,
        structured_content=_structured_content(result),
        meta={"assurance": assurance},
    )


def _structured_content(result: UserRecord | list[UserRecord]) -> dict:
    """Replicate fastmcp's own automatic wrapping so returning a ToolResult
    (needed to carry the assurance meta) doesn't change the wire shape:
    fastmcp wraps a non-dict return (like list[UserRecord]) under a "result"
    key, but leaves a single UserRecord's own dict as-is."""
    if isinstance(result, list):
        return {"result": [item.model_dump(mode="json") for item in result]}
    return result.model_dump(mode="json")
