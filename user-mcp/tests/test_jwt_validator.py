from __future__ import annotations

import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from auth.jwt_validator import JwtValidator, extract_identity
from errors import AppError


@pytest.fixture(scope="module")
def rsa_keys():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_key = private_key.public_key()
    return {
        "private_pem": private_pem,
        "public_key": public_key,
        "kid": "test-kid",
    }


def _sign(rsa_keys, claims: dict[str, Any]) -> str:
    return jwt.encode(
        claims,
        rsa_keys["private_pem"],
        algorithm="RS256",
        headers={"kid": rsa_keys["kid"]},
    )


def _make_validator(rsa_keys, *, audience="user-mcp", issuer="https://verify.example") -> JwtValidator:
    validator = object.__new__(JwtValidator)
    validator._jwks_client = _StubJwksClient(rsa_keys["public_key"])
    validator._audience = audience
    validator._issuer = issuer
    validator._algorithms = ["RS256"]
    validator._leeway = 30
    return validator


class _StubJwksClient:
    def __init__(self, public_key):
        self._key = public_key

    def get_signing_key_from_jwt(self, _token):
        class _Key:
            def __init__(self, key):
                self.key = key

        return _Key(self._key)


def test_validate_accepts_valid_token(rsa_keys):
    now = int(time.time())
    token = _sign(
        rsa_keys,
        {
            "iss": "https://verify.example",
            "aud": "user-mcp",
            "iat": now,
            "exp": now + 300,
            "preferred_username": "alice@example.com",
            "act": {"agent_id": "agent-42"},
            "scope": "users.read users.write",
            "sub": "user-1",
        },
    )
    validator = _make_validator(rsa_keys)
    claims = validator.validate(token)
    identity = extract_identity(claims)
    assert identity["preferred_username"] == "alice@example.com"
    assert identity["agent_id"] == "agent-42"
    assert identity["scope"] == "users.read users.write"


def test_validate_rejects_expired_token(rsa_keys):
    now = int(time.time())
    token = _sign(
        rsa_keys,
        {
            "iss": "https://verify.example",
            "aud": "user-mcp",
            "iat": now - 600,
            "exp": now - 60,
            "preferred_username": "alice@example.com",
        },
    )
    validator = _make_validator(rsa_keys)
    with pytest.raises(AppError) as exc:
        validator.validate(token)
    assert exc.value.error == "expired_token"
    assert exc.value.status_code == 401


def test_validate_rejects_wrong_audience(rsa_keys):
    now = int(time.time())
    token = _sign(
        rsa_keys,
        {
            "iss": "https://verify.example",
            "aud": "some-other-aud",
            "iat": now,
            "exp": now + 300,
        },
    )
    validator = _make_validator(rsa_keys)
    with pytest.raises(AppError) as exc:
        validator.validate(token)
    assert exc.value.error == "invalid_audience"


def test_validate_rejects_wrong_issuer(rsa_keys):
    now = int(time.time())
    token = _sign(
        rsa_keys,
        {
            "iss": "https://attacker.example",
            "aud": "user-mcp",
            "iat": now,
            "exp": now + 300,
        },
    )
    validator = _make_validator(rsa_keys)
    with pytest.raises(AppError) as exc:
        validator.validate(token)
    assert exc.value.error == "invalid_issuer"


def test_validate_rejects_missing_required_claim(rsa_keys):
    now = int(time.time())
    # Missing `aud`.
    token = _sign(
        rsa_keys,
        {
            "iss": "https://verify.example",
            "iat": now,
            "exp": now + 300,
        },
    )
    validator = _make_validator(rsa_keys)
    with pytest.raises(AppError) as exc:
        validator.validate(token)
    assert exc.value.error in {"invalid_audience", "invalid_token"}


def test_extract_identity_handles_scp_array():
    identity = extract_identity(
        {
            "preferred_username": "bob",
            "act": {"agent_id": "agent-9"},
            "scp": ["users.read", "users.write"],
            "sub": "user-2",
        }
    )
    assert identity["scope"] == "users.read users.write"
    assert identity["agent_id"] == "agent-9"


def test_extract_identity_handles_missing_actor():
    identity = extract_identity({"preferred_username": "bob"})
    assert identity["agent_id"] is None


def test_extract_identity_handles_string_scope():
    identity = extract_identity(
        {
            "scope": "users.read",
        }
    )
    assert identity["scope"] == "users.read"


def test_decode_unverified_reads_claims_without_signature():
    from auth.jwt_validator import decode_unverified

    token = jwt.encode(
        {"preferred_username": "alice", "scope": "users.read", "sub": "1"},
        "secret",
        algorithm="HS256",
    )
    claims = decode_unverified(token)
    assert claims["preferred_username"] == "alice"
    assert claims["scope"] == "users.read"


def test_decode_unverified_rejects_malformed():
    from auth.jwt_validator import decode_unverified

    with pytest.raises(AppError) as exc:
        decode_unverified("not-a-jwt")
    assert exc.value.error == "invalid_token"


async def test_trust_gateway_middleware_binds_identity_and_pep_headers():
    import logging

    from auth.context import current_obo_user, current_obo_token, current_pep_assurance
    from auth.jwt_validator import JwtAuthMiddleware

    captured = {}

    async def app(scope, receive, send):
        captured["user"] = current_obo_user.get()
        captured["token"] = current_obo_token.get()
        captured["pep"] = current_pep_assurance.get()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    token = jwt.encode(
        {"preferred_username": "alice", "scope": "users.read", "sub": "1"},
        "secret",
        algorithm="HS256",
    )
    mw = JwtAuthMiddleware(
        app,
        validator=None,
        bypass_auth=False,
        logger=logging.getLogger("test.auth"),
        trust_gateway=True,
    )
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": [
            (b"authorization", f"Bearer {token}".encode("latin-1")),
            (b"x-pep-decision", b"ALLOW"),
            (b"x-pep-loa", b"2"),
            (b"x-pep-required-loa", b"2"),
        ],
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    messages = []

    async def send(message):
        messages.append(message)

    await mw(scope, receive, send)
    assert captured["user"] == "alice"
    assert captured["token"] == token
    assert captured["pep"] == {
        "decision": "ALLOW",
        "current_loa": 2,
        "required_loa": 2,
    }
    assert messages[0]["status"] == 200
