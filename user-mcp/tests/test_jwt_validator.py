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
