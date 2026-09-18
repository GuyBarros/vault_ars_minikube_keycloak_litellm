"""Structured logging helpers for the Streamlit app."""

from __future__ import annotations

import json
import logging
import os
import socket
import uuid
from collections.abc import Mapping, MutableMapping
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

_APP_LOGGER_NAME = "verify_vault"
_REQUEST_ID_HEADER = "X-Request-ID"
_CLIENT_IP_HEADERS = ("x-forwarded-for", "x-real-ip", "cf-connecting-ip", "remote-addr")
_SERVICE_NAME = os.getenv("LOG_SERVICE_NAME", "verify-vault-web-app")
_ENVIRONMENT = os.getenv("LOG_ENVIRONMENT", os.getenv("ENVIRONMENT", "development"))
_HOSTNAME = socket.gethostname()
_request_id_var: ContextVar[str] = ContextVar("request_id", default="")
_client_ip_var: ContextVar[str] = ContextVar("client_ip", default="-")
_request_path_var: ContextVar[str] = ContextVar("request_path", default="-")
_preferred_username_var: ContextVar[str] = ContextVar("preferred_username", default="")

_STANDARD_LOG_RECORD_FIELDS = frozenset(
    {
        "args",
        "asctime",
        "client_ip",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "preferred_username",
        "process",
        "processName",
        "relativeCreated",
        "request_id",
        "request_path",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


def _resolve_host_ip() -> str:
    """Resolve a stable host IP for the running app instance."""
    candidates: list[str] = []
    try:
        _, _, host_ips = socket.gethostbyname_ex(_HOSTNAME)
        candidates.extend(host_ips)
    except OSError:
        pass

    try:
        for result in socket.getaddrinfo(_HOSTNAME, None, family=socket.AF_INET):
            host_ip = result[4][0]
            if host_ip not in candidates:
                candidates.append(host_ip)
    except OSError:
        pass

    for candidate in candidates:
        if candidate and not candidate.startswith("127."):
            return candidate

    return candidates[0] if candidates else "127.0.0.1"


_HOST_IP = _resolve_host_ip()


class _RequestContextFilter(logging.Filter):
    """Inject request-scoped context into each log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_var.get() or "-"
        record.client_ip = _client_ip_var.get() or "-"
        record.request_path = _request_path_var.get() or "-"
        record.preferred_username = _preferred_username_var.get() or "-"
        return True


class _JsonFormatter(logging.Formatter):
    """Render log records as JSON for structured ingestion."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "service": _SERVICE_NAME,
            "environment": _ENVIRONMENT,
            "host": _HOSTNAME,
            "hostname": _HOSTNAME,
            "host_ip": _HOST_IP,
            "request_id": getattr(record, "request_id", "-"),
            "client_ip": getattr(record, "client_ip", "-"),
            "request_path": getattr(record, "request_path", "-"),
            "message": record.getMessage(),
            "level": record.levelname,
            "severity": record.levelname,
            "logger": record.name,
            "module": record.module,
            "method": record.funcName,
            "function": record.funcName,
            "line": record.lineno,
            "process": record.process,
            "process_name": record.processName,
            "thread": record.thread,
            "thread_name": record.threadName,
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)

        extra = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD_LOG_RECORD_FIELDS and not key.startswith("_")
        }
        if extra:
            payload["extra"] = {
                key: value if isinstance(value, (str, int, float, bool, list, dict, type(None))) else str(value)
                for key, value in extra.items()
            }

        _apply_identity_message_prefix(payload, _preferred_username_var.get() or "")

        return json.dumps(payload, ensure_ascii=True)


def _apply_identity_message_prefix(payload: dict[str, Any], preferred_username: str) -> None:
    """Prepend ``[user=<name>]`` to the message field when an identity is bound."""
    message = payload.get("message")
    if not isinstance(message, str) or not preferred_username:
        return
    payload["message"] = f"[user={preferred_username}] {message}"


def _prepare_handler(handler: logging.Handler, level: int) -> None:
    """Apply the JSON formatter and request filter to a handler."""
    handler.setLevel(level)
    handler.setFormatter(_JsonFormatter())
    if not any(isinstance(existing_filter, _RequestContextFilter) for existing_filter in handler.filters):
        handler.addFilter(_RequestContextFilter())


def _configure_root_logger(level: int) -> logging.Logger:
    """Configure the root logger so framework logs emit structured JSON too."""
    root_logger = logging.getLogger()
    if root_logger.handlers:
        for handler in root_logger.handlers:
            _prepare_handler(handler, level)
    else:
        handler = logging.StreamHandler()
        _prepare_handler(handler, level)
        root_logger.addHandler(handler)

    root_logger.setLevel(level)
    return root_logger


def _configure_named_logger(name: str, level: int) -> None:
    """Ensure framework loggers bubble up to the root JSON handler."""
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.setLevel(level)
    logger.propagate = True


def configure_logging() -> None:
    """Configure application and framework loggers once per process."""
    root_logger = logging.getLogger()
    if getattr(root_logger, "_request_logging_configured", False):
        return

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    _configure_root_logger(level)

    app_logger = logging.getLogger(_APP_LOGGER_NAME)
    app_logger.handlers.clear()
    app_logger.setLevel(level)
    app_logger.propagate = True

    for logger_name in ("streamlit", "tornado", "watchdog", "py.warnings"):
        _configure_named_logger(logger_name, level)

    logging.captureWarnings(True)
    root_logger._request_logging_configured = True  # type: ignore[attr-defined]


def get_logger(name: str) -> logging.Logger:
    """Return a child logger of the app logger."""
    configure_logging()
    return logging.getLogger(f"{_APP_LOGGER_NAME}.{name}")


def _normalize_headers(headers: Mapping[str, Any] | None) -> dict[str, str]:
    """Return a lowercase header map for stable lookups."""
    if not headers:
        return {}

    normalized: dict[str, str] = {}
    for key in headers:
        value = headers.get(key)
        if value is not None:
            normalized[str(key).lower()] = str(value)
    return normalized


def _extract_client_ip(headers: Mapping[str, Any] | None) -> str:
    """Resolve the originating client IP from proxy-aware headers when available."""
    normalized_headers = _normalize_headers(headers)
    for header_name in _CLIENT_IP_HEADERS:
        value = normalized_headers.get(header_name, "").strip()
        if value:
            return value.split(",")[0].strip()
    return "-"


def _sanitize_request_path(url: str = "") -> str:
    """Return a request path without query parameters or fragments."""
    if not url:
        return "streamlit-session"

    parsed_url = urlsplit(url)
    return parsed_url.path or "/"


def resolve_request_id(headers: Mapping[str, Any] | None = None) -> str:
    """Resolve the current request ID from headers or generate a fallback."""
    normalized_headers = _normalize_headers(headers)
    incoming_request_id = normalized_headers.get(_REQUEST_ID_HEADER.lower(), "").strip()
    request_id = incoming_request_id or str(uuid.uuid4())
    _request_id_var.set(request_id)
    return request_id


def bind_request_context(
    session_state: MutableMapping[str, Any],
    headers: Mapping[str, Any] | None,
    url: str = "",
    preferred_username: str = "",
) -> str:
    """Persist and bind the current request ID for this Streamlit session."""
    normalized_headers = _normalize_headers(headers)
    incoming_request_id = normalized_headers.get(_REQUEST_ID_HEADER.lower(), "").strip()
    request_id = incoming_request_id or str(session_state.get("request_id") or "") or str(
        uuid.uuid4()
    )

    request_path = _sanitize_request_path(url)
    client_ip = _extract_client_ip(headers)

    _request_id_var.set(request_id)
    _client_ip_var.set(client_ip)
    _request_path_var.set(request_path)
    _preferred_username_var.set(preferred_username)
    session_state["request_id"] = request_id
    session_state["preferred_username"] = preferred_username

    if session_state.get("_logged_request_id") != request_id:
        logger = get_logger("request")
        source = "header" if incoming_request_id else "generated"
        logger.debug("Bound request context using %s request ID", source)
        session_state["_logged_request_id"] = request_id

    return request_id


def rotate_request_id(session_state: MutableMapping[str, Any]) -> str:
    """Generate a fresh request ID and bind it for the current session."""
    new_request_id = str(uuid.uuid4())
    _request_id_var.set(new_request_id)
    _preferred_username_var.set(str(session_state.get("preferred_username") or ""))
    session_state["request_id"] = new_request_id
    session_state.pop("_logged_request_id", None)
    return new_request_id


def get_request_id() -> str:
    """Return the active request ID, creating one if needed."""
    request_id = _request_id_var.get().strip()
    if request_id:
        return request_id
    return resolve_request_id()


def build_outbound_headers(headers: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return headers with the active request ID propagated upstream."""
    outbound_headers = dict(headers or {})
    outbound_headers[_REQUEST_ID_HEADER] = get_request_id()
    return outbound_headers
