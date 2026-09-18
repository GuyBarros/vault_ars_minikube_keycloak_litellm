from pathlib import Path

import hvac
import hvac.exceptions

from app_logging.logger import get_logger
from config.settings import settings
from exceptions.errors import (
    CatalogConflictError,
    VaultAuthError,
    VaultUnavailableError,
)

logger = get_logger(__name__)


class VaultCatalogClient:
    """KV v2 read/write wrapper for the MCP authz catalog.

    The Vault token is rendered onto disk by the vault-agent sidecar
    (annotation `vault.hashicorp.com/agent-inject-token: "true"` → file
    at `settings.vault_token_path`). We re-read it per call so token
    rotation is transparent to the API.
    """

    def __init__(self) -> None:
        self._mount = settings.vault_kv_mount
        self._path = settings.vault_kv_path
        self._token_path = Path(settings.vault_token_path)
        self._verify: bool | str = (
            settings.vault_ca_bundle
            if settings.vault_ca_bundle
            else settings.vault_tls_verify
        )

    def _client(self) -> hvac.Client:
        try:
            token = self._token_path.read_text().strip()
        except OSError as exc:
            raise VaultAuthError(
                f"Vault token file not readable at {self._token_path}: {exc}"
            ) from exc
        if not token:
            raise VaultAuthError(f"Vault token file at {self._token_path} is empty")
        client = hvac.Client(url=settings.vault_addr, verify=self._verify, token=token)
        if not client.is_authenticated():
            raise VaultAuthError("Vault token is invalid or expired")
        return client

    def read(self) -> tuple[dict, int, str | None]:
        """Return (catalog_dict, version, created_time)."""
        try:
            resp = self._client().secrets.kv.v2.read_secret_version(
                mount_point=self._mount,
                path=self._path,
                raise_on_deleted_version=False,
            )
        except hvac.exceptions.Forbidden as exc:
            raise VaultAuthError(str(exc)) from exc
        except hvac.exceptions.VaultError as exc:
            raise VaultUnavailableError(str(exc)) from exc

        data = resp["data"]
        return data["data"], data["metadata"]["version"], data["metadata"].get("created_time")

    def write(self, catalog: dict, expected_version: int | None) -> tuple[int, str | None]:
        """Write the catalog; honour expected_version for CAS.

        Returns (new_version, created_time).
        """
        try:
            kwargs: dict = {"mount_point": self._mount, "path": self._path, "secret": catalog}
            if expected_version is not None:
                kwargs["cas"] = expected_version
            resp = self._client().secrets.kv.v2.create_or_update_secret(**kwargs)
        except hvac.exceptions.InvalidRequest as exc:
            # KV v2 returns 400 with "check-and-set parameter did not match" on CAS mismatch.
            msg = str(exc).lower()
            if "cas" in msg or "check-and-set" in msg or "version" in msg:
                raise CatalogConflictError(str(exc)) from exc
            raise
        except hvac.exceptions.Forbidden as exc:
            raise VaultAuthError(str(exc)) from exc
        except hvac.exceptions.VaultError as exc:
            raise VaultUnavailableError(str(exc)) from exc

        meta = resp["data"]
        return meta["version"], meta.get("created_time")

    def read_version(self, version: int) -> tuple[dict, int, str | None]:
        """Return (catalog_dict, version, created_time) for a specific KV v2 version."""
        try:
            resp = self._client().secrets.kv.v2.read_secret_version(
                mount_point=self._mount,
                path=self._path,
                version=version,
                raise_on_deleted_version=False,
            )
        except hvac.exceptions.Forbidden as exc:
            raise VaultAuthError(str(exc)) from exc
        except hvac.exceptions.VaultError as exc:
            raise VaultUnavailableError(str(exc)) from exc

        data = resp["data"]
        return data["data"], data["metadata"]["version"], data["metadata"].get("created_time")

    def list_versions(self) -> tuple[int, dict[int, dict]]:
        """Return (current_version, versions_map).

        versions_map keys are int version numbers; each value carries
        created_time, deletion_time (empty string when not deleted), and
        destroyed flag. Mirrors Vault KV v2's metadata payload.
        """
        try:
            resp = self._client().secrets.kv.v2.read_secret_metadata(
                mount_point=self._mount,
                path=self._path,
            )
        except hvac.exceptions.Forbidden as exc:
            raise VaultAuthError(str(exc)) from exc
        except hvac.exceptions.VaultError as exc:
            raise VaultUnavailableError(str(exc)) from exc

        meta = resp["data"]
        versions = {
            int(v): {
                "created_time": info.get("created_time"),
                "deletion_time": info.get("deletion_time") or None,
                "destroyed": bool(info.get("destroyed", False)),
            }
            for v, info in (meta.get("versions") or {}).items()
        }
        return int(meta["current_version"]), versions
