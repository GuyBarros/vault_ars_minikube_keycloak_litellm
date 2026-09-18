import logging
import socket
import sys
from typing import Any

import structlog
from starlette.requests import Request
from structlog.processors import CallsiteParameter, CallsiteParameterAdder

SERVICE_NAME = "consul-mcp-authz"
HOSTNAME = socket.gethostname()

try:
    HOST_IP = socket.gethostbyname(HOSTNAME)
except socket.gaierror:
    HOST_IP = None


def _add_standard_fields(
    _: logging.Logger, __: str, event_dict: structlog.typing.EventDict
) -> structlog.typing.EventDict:
    event_dict.setdefault("service", SERVICE_NAME)
    event_dict.setdefault("hostname", HOSTNAME)
    if HOST_IP is not None:
        event_dict.setdefault("host_ip", HOST_IP)
    return event_dict


def _add_message_field(
    _: logging.Logger, __: str, event_dict: structlog.typing.EventDict
) -> structlog.typing.EventDict:
    if "message" not in event_dict:
        event = event_dict.get("event")
        if event is not None:
            event_dict["message"] = event
    return event_dict


def _shared_processors() -> list[Any]:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", key="timestamp"),
        CallsiteParameterAdder(
            {
                CallsiteParameter.MODULE,
                CallsiteParameter.FUNC_NAME,
                CallsiteParameter.LINENO,
            }
        ),
        structlog.processors.StackInfoRenderer(),
        _add_standard_fields,
        _add_message_field,
    ]


def configure_logging(log_level: str = "INFO") -> None:
    resolved_level = getattr(logging, log_level.upper(), logging.INFO)
    shared_processors = _shared_processors()
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        pass_foreign_args=True,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(resolved_level)

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


def bind_request_context(request: Request, request_id: str) -> None:
    forwarded_for = request.headers.get("x-forwarded-for")
    client_ip = (
        forwarded_for.split(",")[0].strip()
        if forwarded_for
        else request.client.host if request.client else None
    )
    context = {
        "request_id": request_id,
        "http_method": request.method,
        "http_path": request.url.path,
    }
    if client_ip:
        context["client_ip"] = client_ip
    structlog.contextvars.bind_contextvars(**context)


def clear_request_id() -> None:
    structlog.contextvars.clear_contextvars()
