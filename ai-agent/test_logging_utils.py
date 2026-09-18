import json
import logging

from logging_utils import (
    JsonLogFormatter,
    UvicornAccessJsonFormatter,
    build_uvicorn_log_config,
    configure_logging,
)


def test_configure_logging_sets_json_formatter_on_root_logger():
    configure_logging("debug")

    root_logger = logging.getLogger()

    assert root_logger.level == logging.DEBUG
    assert any(
        isinstance(handler.formatter, JsonLogFormatter)
        for handler in root_logger.handlers
    )


def test_json_log_formatter_preserves_structured_json_messages():
    formatter = JsonLogFormatter()
    record = logging.LogRecord(
        name="agent_api",
        level=logging.INFO,
        pathname=__file__,
        lineno=42,
        msg=json.dumps({"event": "request_received", "request_id": "abc-123"}),
        args=(),
        exc_info=None,
    )

    payload = json.loads(formatter.format(record))

    assert payload["event"] == "request_received"
    assert payload["request_id"] == "abc-123"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "agent_api"
    assert payload["hostname"]
    assert "host_ip" in payload


def test_uvicorn_access_json_formatter_outputs_structured_access_fields():
    formatter = UvicornAccessJsonFormatter()
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=64,
        msg='%s - "%s %s HTTP/%s" %s',
        args=("127.0.0.1:53214", "GET", "/health", "1.1", 200),
        exc_info=None,
    )

    payload = json.loads(formatter.format(record))

    assert payload["event"] == "http_access"
    assert payload["client_addr"] == "127.0.0.1:53214"
    assert payload["http_method"] == "GET"
    assert payload["path"] == "/health"
    assert payload["http_version"] == "1.1"
    assert payload["status_code"] == 200
    assert payload["logger"] == "uvicorn.access"


def test_build_uvicorn_log_config_uses_json_formatters():
    log_config = build_uvicorn_log_config("debug")

    assert log_config["formatters"]["json"]["()"] == "logging_utils.JsonLogFormatter"
    assert (
        log_config["formatters"]["access_json"]["()"]
        == "logging_utils.UvicornAccessJsonFormatter"
    )
    assert log_config["loggers"]["uvicorn"]["level"] == "DEBUG"
    assert log_config["loggers"]["uvicorn.access"]["handlers"] == ["access"]
