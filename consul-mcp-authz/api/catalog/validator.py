import json
import subprocess
import tempfile
from pathlib import Path

from app_logging.logger import get_logger
from config.settings import settings
from exceptions.errors import CatalogValidationError

logger = get_logger(__name__)


def validate(candidate: dict) -> None:
    """Validate that *candidate* (the rules document) parses cleanly under
    the real policy.

    Strategy:
      1. Wrap the candidate in the same `{"policy":{"mcp_authz":{...}}}`
         envelope that vault-agent renders on the OPA pod.
      2. `opa eval -d <wrapped> -d <policy_dir> 'data.policy.mcp_authz.rules'`
         — confirms the data document loads, the policy still compiles, and
         the rules path resolves to an object.
      3. Reject if the eval errors or the resolved value is not a JSON
         object.

    Raises:
        CatalogValidationError: candidate is structurally invalid.
    """
    envelope = {"policy": {"mcp_authz": candidate}}

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(envelope, f)
        data_path = Path(f.name)

    try:
        result = subprocess.run(
            [
                settings.opa_bin,
                "eval",
                "--data", str(data_path),
                "--data", settings.policy_dir,
                "--format", "json",
                "data.policy.mcp_authz.rules",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError as exc:
        raise CatalogValidationError(
            f"opa binary not found at {settings.opa_bin}: {exc}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise CatalogValidationError("opa eval timed out") from exc
    finally:
        data_path.unlink(missing_ok=True)

    if result.returncode != 0:
        logger.warning("validator_opa_eval_failed", stderr=result.stderr)
        raise CatalogValidationError(
            f"opa eval rejected the candidate: {result.stderr.strip()}"
        )

    try:
        payload = json.loads(result.stdout)
        value = payload["result"][0]["expressions"][0]["value"]
    except (json.JSONDecodeError, KeyError, IndexError) as exc:
        raise CatalogValidationError(
            f"unexpected opa eval output: {result.stdout!r}"
        ) from exc

    if not isinstance(value, dict):
        raise CatalogValidationError(
            f"data.policy.mcp_authz.rules resolved to {type(value).__name__}, expected object"
        )
