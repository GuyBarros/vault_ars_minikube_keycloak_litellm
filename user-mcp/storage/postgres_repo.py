from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any, AsyncIterator

import asyncpg

from auth.context import (
    current_obo_groups,
    current_obo_scope,
    current_obo_token,
    current_obo_user,
    current_pep_assurance,
)
from ciba_client import CibaClient
from errors import AppError
from loa import ALLOW, DENY, EXPIRED, LOA_BASELINE, LOA_ELEVATED, STEP_UP_REQUIRED, log_pdp_decision
from logging_utils import bind_log_context, log_event
from models import UserRecord
from storage.base import UserRepository
from vault_client import VaultClient

LOGGER = logging.getLogger("user_mcp.storage.postgres")

_COLUMNS = (
    "email",
    "first_name",
    "last_name",
    "ssn",
    "phone",
    "credit_card_number",
    "ip_address",
)
_SELECT = ", ".join(_COLUMNS)

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS users (
        email              TEXT PRIMARY KEY,
        first_name         TEXT,
        last_name          TEXT,
        ssn                TEXT,
        phone              TEXT,
        credit_card_number TEXT,
        ip_address         TEXT
    );
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS users_email_lower_uidx ON users (lower(email));",
    "CREATE INDEX IF NOT EXISTS users_first_name_lower_idx ON users (lower(first_name));",
)

_SCOPE_WRITE = "users.write"
_SCOPE_READ = "users.read"

# Vault Transform role/transformations set up in keycloak.sh. users.write is
# writer+admin per token-exchange's SCOPE_REQUIREMENTS, so create/update/delete
# never need masking - only the two read tools do.
_ADMIN_GROUP = "admin"
_VAULT_TRANSFORM_ROLE = "user-mcp-transform"
_PII_TRANSFORMATIONS = {
    "ssn": "user-mcp-ssn",
    "credit_card_number": "user-mcp-credit-card",
    "phone": "user-mcp-phone",
    "ip_address": "user-mcp-ip-address",
}

# Task-local, not an instance attribute: PostgresUserRepository is a
# process-wide singleton (see storage/factory.py) shared across concurrent
# requests, so a plain `self.last_assurance` would let one request's PDP
# decision leak into another's response under real concurrency.
_last_assurance: ContextVar[dict | None] = ContextVar("_last_assurance", default=None)


class PostgresUserRepository(UserRepository):
    """Postgres-backed user repository with two credential modes:

    - ``direct``: a long-lived asyncpg pool authenticated with static
      USER_MCP_DB_USER / USER_MCP_DB_PASSWORD. Intended only for connectivity
      testing.
    - ``vault``: every request presents the caller's Keycloak OBO (or CIBA)
      JWT as ``X-Vault-Token``. Vault 2.1's OAuth Resource Server validates
      it inline against the human's baseline ACL intersected with the
      ai-agent's Agent Registry ceiling. jwt-keycloak login is used only to
      probe the CIBA-required-by-policy ACL switch.
    """

    def __init__(
        self,
        pg_url: str,
        auth_mode: str,
        auto_migrate: bool = False,
        # direct mode
        db_user: str = "",
        db_password: str = "",
        # vault mode
        vault_client: VaultClient | None = None,
        vault_jwt_read_role: str = "",
        vault_jwt_write_role: str = "",
        vault_db_read_path: str = "",
        vault_db_write_path: str = "",
        ciba_client: CibaClient | None = None,
    ):
        if not pg_url:
            raise AppError(
                500,
                "configuration_error",
                "USER_MCP_PG_URL is required when USER_BACKEND=postgres.",
            )
        if auth_mode not in ("direct", "vault"):
            raise AppError(
                500,
                "configuration_error",
                f"Unsupported USER_MCP_DB_AUTH_MODE: {auth_mode}",
            )
        if auth_mode == "direct" and (not db_user or not db_password):
            raise AppError(
                500,
                "configuration_error",
                "USER_MCP_DB_USER and USER_MCP_DB_PASSWORD are required when "
                "USER_MCP_DB_AUTH_MODE=direct.",
            )
        if auth_mode == "vault":
            if vault_client is None:
                raise AppError(
                    500,
                    "configuration_error",
                    "Vault client is required when USER_MCP_DB_AUTH_MODE=vault.",
                )
            if not vault_jwt_read_role or not vault_jwt_write_role:
                raise AppError(
                    500,
                    "configuration_error",
                    "Vault JWT read/write role names are required.",
                )
            if not vault_db_read_path or not vault_db_write_path:
                raise AppError(
                    500,
                    "configuration_error",
                    "Vault DB credential paths are required.",
                )

        self._pg_url = pg_url
        self._auth_mode = auth_mode
        self._auto_migrate = auto_migrate
        self._db_user = db_user
        self._db_password = db_password
        self._vault = vault_client
        self._jwt_read_role = vault_jwt_read_role
        self._jwt_write_role = vault_jwt_write_role
        self._db_read_path = vault_db_read_path
        self._db_write_path = vault_db_write_path
        self._ciba = ciba_client
        self._pool: asyncpg.Pool | None = None

    async def startup(self) -> None:
        if self._auth_mode == "direct":
            log_event(
                LOGGER,
                "postgres_pool_init",
                message="Initializing Postgres connection pool (direct mode)",
            )
            self._pool = await asyncpg.create_pool(
                dsn=self._pg_url,
                user=self._db_user,
                password=self._db_password,
                min_size=1,
                max_size=10,
            )
            if self._auto_migrate:
                async with self._pool.acquire() as conn:
                    async with conn.transaction():
                        for statement in _DDL:
                            await conn.execute(statement)
        else:
            log_event(
                LOGGER,
                "postgres_vault_mode_ready",
                message="Postgres repository ready (vault mode, per-request creds)",
            )

    async def shutdown(self) -> None:
        if self._pool is not None:
            log_event(
                LOGGER,
                "postgres_pool_close",
                message="Closing Postgres connection pool",
            )
            await self._pool.close()
            self._pool = None

    def get_last_assurance(self) -> dict | None:
        return _last_assurance.get()

    @asynccontextmanager
    async def _acquire(
        self, *, write: bool = False, action: str = ""
    ) -> AsyncIterator[asyncpg.Connection]:
        if self._auth_mode == "direct":
            if self._pool is None:
                raise AppError(500, "agent_error", "Postgres pool not initialized.")
            async with self._pool.acquire() as conn:
                bind_log_context(db_username=self._db_user)
                log_event(
                    LOGGER,
                    "db_call",
                    level=logging.DEBUG,
                    message="Postgres connection ready (direct mode)",
                    auth_mode="direct",
                    db_username=self._db_user,
                )
                yield conn
            return

        # vault mode: present the Keycloak OBO/CIBA JWT as X-Vault-Token.
        # Vault's OAuth Resource Server validates it inline; jwt-keycloak
        # login is only used to probe CIBA policy per tool (create/delete
        # HITL, update/read silent OBO — see keycloak.sh's ciba-* policies).
        user = current_obo_user.get(None)
        obo_token = current_obo_token.get(None)
        scope = current_obo_scope.get(None) or ""
        if not user or not obo_token:
            raise AppError(
                401,
                "invalid_request",
                "A validated user OBO token is required to obtain database "
                "credentials in vault mode.",
            )
        jwt_role, db_creds_path = self._select_vault_targets(scope, write=write)

        assert self._vault is not None
        vault_jwt = await self._jwt_for_vault(obo_token, jwt_role, user, action=action)
        creds = await self._vault.read_database_creds(vault_jwt, db_creds_path)

        try:
            conn = await asyncpg.connect(
                dsn=self._pg_url,
                user=creds.username,
                password=creds.password,
            )
        except (OSError, asyncpg.PostgresError) as exc:
            log_event(
                LOGGER,
                "db_connection_failed",
                level=logging.ERROR,
                message=f"Postgres connection failed: {exc}",
                auth_mode="vault",
                connection_status="failed",
                db_username=creds.username,
            )
            raise AppError(
                502,
                "agent_error",
                f"Failed to connect to Postgres with Vault-issued credentials: {exc}",
            ) from exc

        bind_log_context(db_username=creds.username)
        log_event(
            LOGGER,
            "db_call",
            level=logging.INFO,
            message="Postgres connection ready (vault mode)",
            auth_mode="vault",
            db_username=creds.username,
            vault_role=jwt_role,
            db_creds_path=db_creds_path,
            lease_id=creds.lease_id,
            lease_duration=creds.lease_duration,
        )
        try:
            yield conn
        finally:
            await conn.close()
            if creds.lease_id:
                try:
                    await self._vault.revoke_lease(vault_jwt, creds.lease_id)
                except AppError as exc:
                    log_event(
                        LOGGER,
                        "vault_lease_revoke_failed",
                        level=logging.WARNING,
                        message=f"Failed to revoke Vault lease: {exc.message}",
                        lease_id=creds.lease_id,
                    )

    async def _jwt_for_vault(
        self, obo_token: str, jwt_role: str, user: str, *, action: str
    ) -> str:
        """Return the JWT Vault's OAuth Resource Server should see for this
        tool call: the OBO token itself for silent-OBO actions, or a
        Keycloak CIBA-approved JWT when the ciba/<action>/<user> ACL policy
        requires a human to approve first (see loa.py for the PDP_Decision
        audit-log vocabulary this emits)."""
        assert self._vault is not None
        if self._ciba is None:
            # Runtime mode: LiteLLM already probed Vault and completed CIBA.
            pep = current_pep_assurance.get(None) or {}
            current_loa = int(pep.get("current_loa") or LOA_BASELINE)
            required_loa = int(pep.get("required_loa") or current_loa)
            _last_assurance.set(
                {
                    "tool": action,
                    "decision": pep.get("decision") or ALLOW,
                    "current_loa": current_loa,
                    "required_loa": required_loa,
                }
            )
            return obo_token
        parent = await self._vault.login_with_jwt(obo_token, jwt_role)
        need = await self._vault.ciba_required_by_policy(parent, action=action, user=user)
        if not need:
            # No PDP_Decision log line here deliberately - this is the common
            # case (every read, most writes) and logging it at the same
            # level as an actual step-up would bury the signal. last_assurance
            # is still recorded so the caller sees a real LoA 1 for this call,
            # not just silence.
            _last_assurance.set(
                {
                    "tool": action,
                    "decision": ALLOW,
                    "current_loa": LOA_BASELINE,
                    "required_loa": LOA_BASELINE,
                }
            )
            return obo_token

        log_pdp_decision(
            LOGGER,
            tool_name=action,
            decision=STEP_UP_REQUIRED,
            current_loa=LOA_BASELINE,
            required_loa=LOA_ELEVATED,
            preferred_username=user,
        )
        try:
            ciba_jwt = await self._request_ciba(user, action=action)
        except AppError as exc:
            expired = exc.error == "invalid_request" and "expired" in exc.message.lower()
            decision = EXPIRED if expired else DENY
            log_pdp_decision(
                LOGGER,
                tool_name=action,
                decision=decision,
                current_loa=LOA_BASELINE,
                required_loa=LOA_ELEVATED,
                preferred_username=user,
            )
            _last_assurance.set(
                {
                    "tool": action,
                    "decision": decision,
                    "current_loa": LOA_BASELINE,
                    "required_loa": LOA_ELEVATED,
                }
            )
            raise
        log_pdp_decision(
            LOGGER,
            tool_name=action,
            decision=ALLOW,
            current_loa=LOA_ELEVATED,
            required_loa=LOA_ELEVATED,
            preferred_username=user,
        )
        _last_assurance.set(
            {
                "tool": action,
                "decision": ALLOW,
                "current_loa": LOA_ELEVATED,
                "required_loa": LOA_ELEVATED,
            }
        )
        return ciba_jwt

    async def _request_ciba(self, user: str, *, action: str) -> str:
        if self._ciba is None:
            raise AppError(
                403,
                "invalid_request",
                f"Vault policy requires CIBA for action {action} but CIBA is "
                "not configured on user-mcp.",
            )
        write_actions = {"create_user", "delete_user_by_email", "update_user_by_email"}
        scope = "openid users.write" if action in write_actions else "openid users.read"
        return await self._ciba.fetch_access_token(
            login_hint=user,
            binding_message=action,
            scope=scope,
        )

    def _select_vault_targets(
        self, scope: str, *, write: bool
    ) -> tuple[str, str]:
        scopes = {part for part in scope.split() if part}
        if write:
            if _SCOPE_WRITE not in scopes:
                raise AppError(
                    403,
                    "invalid_request",
                    f"OBO token scope must include '{_SCOPE_WRITE}' "
                    "to obtain write database credentials.",
                )
            return self._jwt_write_role, self._db_write_path
        if _SCOPE_READ not in scopes and _SCOPE_WRITE not in scopes:
            raise AppError(
                403,
                "invalid_request",
                f"OBO token scope must include '{_SCOPE_READ}' or '{_SCOPE_WRITE}' "
                "to obtain database credentials.",
            )
        return self._jwt_read_role, self._db_read_path

    async def list_all(self) -> list[UserRecord]:
        async with self._acquire(action="list_all_users") as conn:
            rows = await conn.fetch(f"SELECT {_SELECT} FROM users ORDER BY email")
        users = [UserRecord.model_validate(_row_to_dict(r)) for r in rows]
        return await self._mask_pii(users)

    async def search_by_first_name(self, first_name: str) -> list[UserRecord]:
        async with self._acquire(action="search_users_by_first_name") as conn:
            rows = await conn.fetch(
                f"SELECT {_SELECT} FROM users WHERE lower(first_name) = lower($1) ORDER BY email",
                first_name.strip(),
            )
        users = [UserRecord.model_validate(_row_to_dict(r)) for r in rows]
        return await self._mask_pii(users)

    async def _mask_pii(self, users: list[UserRecord]) -> list[UserRecord]:
        """Mask ssn/phone/credit_card_number/ip_address for every caller
        except the admin group, via Vault Transform's one-way masking
        transformation (see keycloak.sh's user-mcp-transform role).

        Best-effort: a masking failure logs a warning and leaves that field
        as returned by Postgres rather than failing the whole read - this is
        a defense-in-depth display control, not the primary authorization
        boundary, so a Vault hiccup shouldn't turn into a hard 500.
        """
        if _ADMIN_GROUP in (current_obo_groups.get(None) or []):
            return users
        if self._vault is None:
            return users
        obo_token = current_obo_token.get(None)
        if not obo_token:
            return users

        masked: list[UserRecord] = []
        for user in users:
            updates: dict[str, str] = {}
            for field, transformation in _PII_TRANSFORMATIONS.items():
                value = getattr(user, field, None)
                if not value:
                    continue
                try:
                    updates[field] = await self._vault.transform_encode(
                        obo_token, _VAULT_TRANSFORM_ROLE, transformation, value
                    )
                except AppError as exc:
                    log_event(
                        LOGGER,
                        "vault_transform_encode_failed",
                        level=logging.WARNING,
                        message=f"Failed to mask {field}: {exc.message}",
                        field=field,
                    )
            masked.append(user.model_copy(update=updates) if updates else user)
        return masked

    async def create(self, user: UserRecord) -> UserRecord:
        params = _user_to_params(user)
        try:
            async with self._acquire(write=True, action="create_user") as conn:
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO users ({_SELECT})
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    RETURNING {_SELECT}
                    """,
                    *params,
                )
        except asyncpg.UniqueViolationError as exc:
            raise AppError(
                400,
                "invalid_request",
                f"User already exists for email: {user.email}",
            ) from exc
        return UserRecord.model_validate(_row_to_dict(row))

    async def delete_by_email(self, email: str) -> UserRecord:
        async with self._acquire(write=True, action="delete_user_by_email") as conn:
            row = await conn.fetchrow(
                f"DELETE FROM users WHERE lower(email) = lower($1) RETURNING {_SELECT}",
                email.strip(),
            )
        if row is None:
            raise AppError(404, "invalid_request", f"User not found for email: {email}")
        return UserRecord.model_validate(_row_to_dict(row))

    async def update_by_email(self, email: str, user: UserRecord) -> UserRecord:
        # Partial update: only touch columns the caller actually provided
        # with a non-null value. The users table declares every column NOT
        # NULL, and LLM tool callers routinely emit explicit `null` for
        # unchanged fields — so exclude_unset alone is not enough; we must
        # also drop None values to avoid wiping NOT NULL columns.
        updates = user.model_dump(exclude_none=True)
        params: list[Any] = [email.strip()]
        set_clauses: list[str] = []
        for col in _COLUMNS:
            if col in updates:
                params.append(updates[col])
                set_clauses.append(f"{col} = ${len(params)}")

        if not set_clauses:
            async with self._acquire(action="update_user_by_email") as conn:
                row = await conn.fetchrow(
                    f"SELECT {_SELECT} FROM users WHERE lower(email) = lower($1)",
                    email.strip(),
                )
            if row is None:
                raise AppError(404, "invalid_request", f"User not found for email: {email}")
            return UserRecord.model_validate(_row_to_dict(row))

        sql = (
            f"UPDATE users SET {', '.join(set_clauses)} "
            f"WHERE lower(email) = lower($1) "
            f"RETURNING {_SELECT}"
        )
        try:
            async with self._acquire(write=True, action="update_user_by_email") as conn:
                row = await conn.fetchrow(sql, *params)
        except asyncpg.UniqueViolationError as exc:
            raise AppError(
                400,
                "invalid_request",
                f"User already exists for email: {user.email}",
            ) from exc
        if row is None:
            raise AppError(404, "invalid_request", f"User not found for email: {email}")
        return UserRecord.model_validate(_row_to_dict(row))


def _user_to_params(user: UserRecord) -> tuple[Any, ...]:
    return (
        user.email,
        user.first_name,
        user.last_name,
        user.ssn,
        user.phone,
        user.credit_card_number,
        user.ip_address,
    )


def _row_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    return {col: row[col] for col in _COLUMNS}
