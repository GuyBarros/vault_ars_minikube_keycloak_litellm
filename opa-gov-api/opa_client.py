import base64
import json
import logging
import os
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

import httpx

from structured_logging import get_logger, log_event

logger = get_logger(__name__)


def _parse_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


@dataclass
class OpaClientConfig:
    base_url: str
    timeout_seconds: float = 5.0
    security_path: str = "/v1/data/app/security"
    masking_path: str = "/v1/data/app/masking/masked_result"
    max_body_bytes: int = 1_048_576
    fail_mode: str = "open"
    mask_unwrap: bool = True

    @classmethod
    def from_env(cls) -> "OpaClientConfig":
        base_url = os.getenv("OPA_BASE_URL")
        if not base_url:
            raise RuntimeError("OPA_BASE_URL environment variable is required")
        return cls(
            base_url=base_url.rstrip("/"),
            timeout_seconds=float(os.getenv("OPA_TIMEOUT_SECONDS", "5")),
            security_path=os.getenv("OPA_SECURITY_PATH", "/v1/data/app/security"),
            masking_path=os.getenv(
                "OPA_MASKING_PATH", "/v1/data/app/masking/masked_result"
            ),
            max_body_bytes=int(os.getenv("MAX_BODY_BYTES", "1048576")),
            fail_mode=os.getenv("OPA_FAIL_MODE", "open").strip().lower(),
            mask_unwrap=_parse_bool_env("OPA_MASK_UNWRAP", True),
        )

    @property
    def fail_open(self) -> bool:
        return self.fail_mode != "closed"


class OpaUpstreamError(RuntimeError):
    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message)
        self.context: dict[str, Any] = context


class OpaMalformedResponseError(OpaUpstreamError):
    pass


@dataclass
class MaskResult:
    value: Any
    is_string: bool
    raw_response: dict[str, Any] = field(default_factory=dict)


def build_async_client(config: OpaClientConfig) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(config.timeout_seconds),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    )


def _encode_input(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _truncate_json_for_log(value: Any, max_chars: int = 500) -> str:
    serialized = json.dumps(value, ensure_ascii=True, default=str)
    if len(serialized) <= max_chars:
        return serialized
    return f"{serialized[:max_chars]}...(truncated)"


class OpaClient:
    def __init__(
        self, config: OpaClientConfig, httpx_client: httpx.AsyncClient
    ) -> None:
        self.config = config
        self._client = httpx_client

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, path: str, text: str) -> dict[str, Any]:
        url = f"{self.config.base_url}{path}"
        payload = {"input": _encode_input(text)}
        started_at = perf_counter()
        try:
            response = await self._client.post(url, json=payload)
        except httpx.HTTPError as exc:
            raise OpaUpstreamError(
                f"OPA request failed: {exc.__class__.__name__}",
                path=path,
                error_class=exc.__class__.__name__,
                error=str(exc),
            ) from exc

        parsed_body: dict[str, Any] | None = None
        json_result_for_log: str | None = None
        try:
            candidate_body = response.json()
            if not isinstance(candidate_body, dict):
                raise OpaMalformedResponseError(
                    "OPA returned JSON body that is not an object",
                    path=path,
                    status=response.status_code,
                    body=response.text[:500],
                )
            parsed_body = candidate_body
            log_result_payload = (
                parsed_body["result"] if "result" in parsed_body else parsed_body
            )
            json_result_for_log = _truncate_json_for_log(log_result_payload)
        except ValueError:
            parsed_body = None

        duration_ms = round((perf_counter() - started_at) * 1000, 3)
        log_event(
            logger,
            logging.DEBUG,
            "opa.upstream.response",
            "Received response from OPA server",
            path=path,
            status_code=response.status_code,
            duration_ms=duration_ms,
            response_bytes=len(response.content),
            response_body=response.text[:500],
            response_json_result=json_result_for_log,
        )

        if response.status_code >= 500 or response.status_code == 408:
            raise OpaUpstreamError(
                "OPA returned upstream error status",
                path=path,
                status=response.status_code,
                body=response.text[:500],
            )
        if response.status_code >= 400:
            raise OpaUpstreamError(
                "OPA rejected the request",
                path=path,
                status=response.status_code,
                body=response.text[:500],
            )

        if parsed_body is None:
            raise OpaMalformedResponseError(
                "OPA returned non-JSON body",
                path=path,
                status=response.status_code,
                body=response.text[:500],
            )
        return parsed_body

    async def check_security(self, text: str) -> dict[str, bool]:
        body = await self._post(self.config.security_path, text)
        result = body.get("result")
        if not isinstance(result, dict):
            raise OpaMalformedResponseError(
                "OPA security response missing 'result' object",
                path=self.config.security_path,
                body=body,
            )
        return {
            "is_injection": bool(result.get("is_injection", False)),
            "is_unsafe": bool(result.get("is_unsafe", False)),
        }

    async def mask(self, text: str) -> MaskResult:
        body = await self._post(self.config.masking_path, text)
        if "result" not in body:
            raise OpaMalformedResponseError(
                "OPA masking response missing 'result' key",
                path=self.config.masking_path,
                body=body,
            )
        value: Any = body["result"] if self.config.mask_unwrap else body
        return MaskResult(
            value=value,
            is_string=isinstance(value, str),
            raw_response=body,
        )

    async def ping(self) -> bool:
        url = f"{self.config.base_url}/health"
        try:
            response = await self._client.get(url, timeout=2.0)
        except httpx.HTTPError:
            return False
        return 200 <= response.status_code < 300
