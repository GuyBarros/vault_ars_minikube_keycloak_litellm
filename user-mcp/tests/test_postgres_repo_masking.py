from __future__ import annotations

from auth.context import bind_request_identity, reset_request_identity
from errors import AppError
from models import UserRecord
from storage.postgres_repo import PostgresUserRepository


class FakeVaultClient:
    def __init__(self, fail_on: set[str] | None = None):
        self.calls: list[tuple[str, str]] = []
        self._fail_on = fail_on or set()

    async def transform_encode(self, client_token, role_name, transformation, value):
        self.calls.append((transformation, value))
        if transformation in self._fail_on:
            raise AppError(502, "agent_error", "boom")
        return f"MASKED({transformation})"


def _make_repo(vault_client) -> PostgresUserRepository:
    return PostgresUserRepository(
        pg_url="postgresql://example/db",
        auth_mode="vault",
        vault_client=vault_client,
        vault_jwt_read_role="user-mcp-oidc-read",
        vault_jwt_write_role="user-mcp-oidc-write",
        vault_db_read_path="database/creds/user-mcp-read-role",
        vault_db_write_path="database/creds/user-mcp-write-role",
    )


def _user(**overrides) -> UserRecord:
    base = dict(
        email="amelia@example.com",
        first_name="Amelia",
        last_name="Wilson",
        ssn="591-00-9242",
        phone="+1-812-669-2470",
        credit_card_number="0388-6685-4496-5569",
        ip_address="19.229.74.152",
    )
    base.update(overrides)
    return UserRecord.model_validate(base)


async def test_admin_group_sees_plaintext():
    vault = FakeVaultClient()
    repo = _make_repo(vault)
    tokens = bind_request_identity(token="tok", scope="users.read", groups=["admin"])
    try:
        result = await repo._mask_pii([_user()])
    finally:
        reset_request_identity(tokens)

    assert result[0].ssn == "591-00-9242"
    assert vault.calls == []


async def test_non_admin_group_gets_masked_pii():
    vault = FakeVaultClient()
    repo = _make_repo(vault)
    tokens = bind_request_identity(token="tok", scope="users.read", groups=["reader"])
    try:
        result = await repo._mask_pii([_user()])
    finally:
        reset_request_identity(tokens)

    masked = result[0]
    assert masked.ssn == "MASKED(user-mcp-ssn)"
    assert masked.credit_card_number == "MASKED(user-mcp-credit-card)"
    assert masked.phone == "MASKED(user-mcp-phone)"
    assert masked.ip_address == "MASKED(user-mcp-ip-address)"
    # Non-PII fields pass through untouched.
    assert masked.email == "amelia@example.com"
    assert masked.first_name == "Amelia"
    assert {t for t, _ in vault.calls} == {
        "user-mcp-ssn",
        "user-mcp-credit-card",
        "user-mcp-phone",
        "user-mcp-ip-address",
    }


async def test_no_groups_claim_defaults_to_masked():
    vault = FakeVaultClient()
    repo = _make_repo(vault)
    tokens = bind_request_identity(token="tok", scope="users.read", groups=None)
    try:
        result = await repo._mask_pii([_user()])
    finally:
        reset_request_identity(tokens)

    assert result[0].ssn != "591-00-9242"


async def test_transform_failure_falls_back_to_plaintext_for_that_field():
    vault = FakeVaultClient(fail_on={"user-mcp-ssn"})
    repo = _make_repo(vault)
    tokens = bind_request_identity(token="tok", scope="users.read", groups=["reader"])
    try:
        result = await repo._mask_pii([_user()])
    finally:
        reset_request_identity(tokens)

    masked = result[0]
    assert masked.ssn == "591-00-9242"  # masking failed, original preserved
    assert masked.phone == "MASKED(user-mcp-phone)"  # other fields still masked


async def test_missing_obo_token_skips_masking():
    vault = FakeVaultClient()
    repo = _make_repo(vault)
    tokens = bind_request_identity(token=None, scope="users.read", groups=["reader"])
    try:
        result = await repo._mask_pii([_user()])
    finally:
        reset_request_identity(tokens)

    assert result[0].ssn == "591-00-9242"
    assert vault.calls == []
