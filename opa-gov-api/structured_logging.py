import json
import logging
import os
import socket
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from typing import Any

REQUEST_ID_CONTEXT: ContextVar[str] = ContextVar("request_id", default="-")
REQUEST_FIELDS_CONTEXT: ContextVar[dict[str, Any]] = ContextVar(
    "request_fields", default={}
)

_RESERVED_LOG_RECORD_FIELDS = {
    "args",
    "asctime",
    "color_message",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "taskName",
    "thread",
    "threadName",
}


def _get_host_name() -> str:
    return os.getenv("HOSTNAME") or socket.gethostname()


def _get_host_ip(host_name: str) -> str:
    configured_host_ip = os.getenv("HOST_IP")
    if configured_host_ip:
        return configured_host_ip

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            resolved_ip = sock.getsockname()[0]
            if resolved_ip:
                return resolved_ip
    except OSError:
        pass

    try:
        address_info = socket.getaddrinfo(
            host_name, None, family=socket.AF_INET, type=socket.SOCK_DGRAM
        )
    except socket.gaierror:
        return "-"

    for address in address_info:
        candidate_ip = address[4][0]
        if candidate_ip and not candidate_ip.startswith("127."):
            return candidate_ip

    return address_info[0][4][0] if address_info else "-"


def _utc_timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


HOST_NAME = _get_host_name()
HOST_IP = _get_host_ip(HOST_NAME)
SERVICE_NAME = (
    os.getenv("LOG_SERVICE_NAME")
    or os.getenv("OTEL_SERVICE_NAME")
    or "opa-gov-api"
)


def _default_event(record: logging.LogRecord) -> str:
    if record.name == "uvicorn.access":
        return "http.access"
    if record.name.startswith("uvicorn"):
        return "uvicorn.log"
    return "application.log"


def _extract_uvicorn_access_fields(record: logging.LogRecord) -> dict[str, Any]:
    if record.name != "uvicorn.access":
        return {}

    if not isinstance(record.args, tuple) or len(record.args) != 5:
        return {}

    client_addr, method, full_path, http_version, status_code = record.args
    payload: dict[str, Any] = {
        "client_ip": str(client_addr),
        "method": str(method),
        "path": str(full_path),
        "http_version": str(http_version),
    }
    try:
        payload["status_code"] = int(status_code)
    except (TypeError, ValueError):
        payload["status_code"] = str(status_code)

    return payload


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": _utc_timestamp(),
            "level": record.levelname,
            "severity": record.levelname,
            "logger": record.name,
            "service": SERVICE_NAME,
            "host_name": HOST_NAME,
            "host_ip": HOST_IP,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "process_id": record.process,
            "thread_name": record.threadName,
            "request_id": getattr(record, "request_id", get_request_id()),
            "event": getattr(record, "event", _default_event(record)),
            "message": record.getMessage(),
        }
        payload.update(get_request_fields())
        payload.update(_extract_uvicorn_access_fields(record))

        for key, value in record.__dict__.items():
            if key.startswith("_") or key in _RESERVED_LOG_RECORD_FIELDS or key in payload:
                continue
            payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(payload, ensure_ascii=True, default=str)


class _ExcludeMetricsAccessLogs(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != "uvicorn.access":
            return True
        if not isinstance(record.args, tuple) or len(record.args) != 5:
            return True
        full_path = str(record.args[2])
        return not full_path.startswith("/metrics")


def configure_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = logging._nameToLevel.get(level_name, logging.INFO)

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(_ExcludeMetricsAccessLogs())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(log_level)
    root_logger.addHandler(handler)

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        app_logger = logging.getLogger(logger_name)
        app_logger.handlers.clear()
        app_logger.propagate = True

    for logger_name in ("httpx", "httpcore"):
        noisy_logger = logging.getLogger(logger_name)
        noisy_logger.setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def get_request_id() -> str:
    return REQUEST_ID_CONTEXT.get()


def set_request_id(request_id: str) -> Token[str]:
    return REQUEST_ID_CONTEXT.set(request_id)


def reset_request_id(token: Token[str]) -> None:
    REQUEST_ID_CONTEXT.reset(token)


def get_request_fields() -> dict[str, Any]:
    return dict(REQUEST_FIELDS_CONTEXT.get())


def set_request_fields(**fields: Any) -> Token[dict[str, Any]]:
    current_fields = get_request_fields()
    current_fields.update(fields)
    return REQUEST_FIELDS_CONTEXT.set(current_fields)


def reset_request_fields(token: Token[dict[str, Any]]) -> None:
    REQUEST_FIELDS_CONTEXT.reset(token)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    message: str,
    **fields: Any,
) -> None:
    logger.log(level, message, extra={"event": event, **fields})
