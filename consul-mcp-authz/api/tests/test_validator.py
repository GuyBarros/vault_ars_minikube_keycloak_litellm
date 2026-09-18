"""Unit tests for catalog.validator.

These run `opa eval` for real, so they require:
  - the `opa` binary on PATH (or MCP_AUTHZ_API_OPA_BIN set), and
  - the policy directory at MCP_AUTHZ_API_POLICY_DIR (default /app/policy);
    in dev, point this at ../opa-mcp-auth/policy.

Skipped automatically when those preconditions are not met.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_POLICY_DIR = REPO_ROOT / "opa-mcp-auth" / "policy"


@pytest.fixture(autouse=True)
def _configure_validator_env(monkeypatch):
    if not os.environ.get("MCP_AUTHZ_API_OPA_BIN"):
        opa = shutil.which("opa")
        if opa:
            monkeypatch.setenv("MCP_AUTHZ_API_OPA_BIN", opa)
    if not os.environ.get("MCP_AUTHZ_API_POLICY_DIR") and DEFAULT_POLICY_DIR.exists():
        monkeypatch.setenv("MCP_AUTHZ_API_POLICY_DIR", str(DEFAULT_POLICY_DIR))


def _preflight_skip() -> None:
    if not shutil.which(os.environ.get("MCP_AUTHZ_API_OPA_BIN", "opa")):
        pytest.skip("opa binary not available")
    policy_dir = Path(os.environ.get("MCP_AUTHZ_API_POLICY_DIR", "/app/policy"))
    if not policy_dir.exists():
        pytest.skip(f"policy dir not found at {policy_dir}")


def test_valid_catalog_passes():
    _preflight_skip()
    # Import inside the test so settings re-reads env from the fixture.
    from catalog.validator import validate

    validate({
        "rules": {
            "default/ai-agent": {
                "default/user-mcp": {"allow": ["list_all_users"]},
            }
        }
    })


def test_empty_rules_passes():
    """Empty rules is structurally valid — policy defaults to deny."""
    _preflight_skip()
    from catalog.validator import validate

    validate({"rules": {}})


def test_rules_as_string_rejected():
    """The Vault stringification footgun (rules='...' as string) must fail."""
    _preflight_skip()
    from catalog.validator import validate
    from exceptions.errors import CatalogValidationError

    with pytest.raises(CatalogValidationError):
        validate({"rules": '{"default/ai-agent": {}}'})


def test_missing_rules_key_rejected():
    _preflight_skip()
    from catalog.validator import validate
    from exceptions.errors import CatalogValidationError

    with pytest.raises(CatalogValidationError):
        # opa eval on data.policy.mcp_authz.rules will resolve to undefined
        # (no result rows), which validator treats as failure.
        validate({"not_rules": {}})
