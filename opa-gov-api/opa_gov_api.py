import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from time import perf_counter

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from opa_client import (
    MaskResult,
    OpaClient,
    OpaClientConfig,
    OpaUpstreamError,
    build_async_client,
)
from structured_logging import (
    configure_logging,
    get_logger,
    log_event,
    reset_request_fields,
    reset_request_id,
    set_request_fields,
    set_request_id,
)
from telemetry import (
    PII_MASKING_SUCCESSFUL_COUNTER,
    VIOLATIONS_COUNTER,
    render_metrics,
)

load_dotenv()
configure_logging()
logger = get_logger(__name__)

BLOCKED_CONTENT_MESSAGE = os.getenv(
    "BLOCKED_CONTENT_MESSAGE",
    "This content was blocked due to security policy violation",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = OpaClientConfig.from_env()
    httpx_client = build_async_client(config)
    client = OpaClient(config, httpx_client)
    app.state.opa_client = client
    app.state.opa_config = config
    log_event(
        logger,
        logging.INFO,
        "opa.client.initialized",
        "OPA client initialized",
        opa_base_url=config.base_url,
        fail_mode=config.fail_mode,
        mask_unwrap=config.mask_unwrap,
    )
    try:
        yield
    finally:
        await client.aclose()
        log_event(
            logger,
            logging.DEBUG,
            "opa.client.closed",
            "OPA client closed",
        )


app = FastAPI(title="OPA Governance API", lifespan=lifespan)


def get_client_ip(http_request: Request) -> str:
    forwarded_for = http_request.headers.get("x-forwarded-for")
    return (
        forwarded_for.split(",")[0].strip()
        if forwarded_for
        else (http_request.client.host if http_request.client else "unknown")
    )


def read_text_body(body: bytes, max_bytes: int) -> str:
    if len(body) > max_bytes:
        raise _BodyTooLargeError(len(body), max_bytes)
    if not body:
        raise ValueError("Request body must not be empty")
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Request body must be valid UTF-8") from exc


def _contains_asterisk_sequence(value: object) -> bool:
    if isinstance(value, str):
        return "*" in value
    if isinstance(value, dict):
        return any(_contains_asterisk_sequence(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_asterisk_sequence(item) for item in value)
    return False


class _BodyTooLargeError(Exception):
    def __init__(self, size: int, limit: int) -> None:
        super().__init__(f"Request body {size} bytes exceeds limit {limit}")
        self.size = size
        self.limit = limit


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
    client_ip = get_client_ip(request)
    request.state.request_id = request_id
    request.state.client_ip = client_ip

    context_token = set_request_id(request_id)
    request_fields_token = set_request_fields(
        method=request.method,
        path=request.url.path,
        client_ip=client_ip,
    )
    started_at = perf_counter()
    should_log_lifecycle = request.url.path != "/metrics"
    if should_log_lifecycle:
        log_event(
            logger,
            logging.DEBUG,
            "http.request.received",
            "HTTP request received",
            request_id=request_id,
        )

    try:
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        duration_ms = round((perf_counter() - started_at) * 1000, 3)
        if should_log_lifecycle:
            log_event(
                logger,
                logging.DEBUG,
                "http.request.completed",
                "HTTP request completed",
                request_id=request_id,
                status_code=response.status_code,
                duration_ms=duration_ms,
            )
        return response
    except Exception:
        duration_ms = round((perf_counter() - started_at) * 1000, 3)
        if should_log_lifecycle:
            logger.exception(
                "HTTP request failed",
                extra={
                    "event": "http.request.failed",
                    "request_id": request_id,
                    "duration_ms": duration_ms,
                },
            )
        raise
    finally:
        reset_request_fields(request_fields_token)
        reset_request_id(context_token)


@app.get("/metrics")
async def metrics_endpoint():
    payload, content_type = render_metrics()
    return Response(content=payload, media_type=content_type)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/readyz")
async def readyz(http_request: Request):
    request_id = http_request.state.request_id
    client: OpaClient = http_request.app.state.opa_client
    reachable = await client.ping()
    if reachable:
        log_event(
            logger,
            logging.DEBUG,
            "opa.readiness.probed",
            "OPA reachable",
            request_id=request_id,
            opa_reachable=True,
        )
        return {"status": "ready"}
    log_event(
        logger,
        logging.WARNING,
        "opa.readiness.probed",
        "OPA not reachable",
        request_id=request_id,
        opa_reachable=False,
    )
    return JSONResponse({"status": "not_ready"}, status_code=503)


def _load_body_or_400(
    body: bytes, max_bytes: int, request_id: str
) -> str:
    try:
        return read_text_body(body, max_bytes)
    except _BodyTooLargeError as exc:
        log_event(
            logger,
            logging.WARNING,
            "opa.request.body_too_large",
            "Request body exceeded size limit",
            request_id=request_id,
            body_bytes=exc.size,
            limit_bytes=exc.limit,
        )
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except ValueError as exc:
        log_event(
            logger,
            logging.WARNING,
            "opa.request.invalid_payload",
            "Invalid request body",
            request_id=request_id,
            reason=str(exc),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/evaluate")
async def evaluate(http_request: Request):
    request_id = http_request.state.request_id
    client: OpaClient = http_request.app.state.opa_client
    config: OpaClientConfig = http_request.app.state.opa_config

    text = _load_body_or_400(
        await http_request.body(), config.max_body_bytes, request_id
    )

    try:
        flags = await client.check_security(text)
    except OpaUpstreamError as exc:
        log_event(
            logger,
            logging.WARNING,
            "opa.upstream.failed",
            "OPA upstream call failed on /evaluate",
            request_id=request_id,
            fail_mode=config.fail_mode,
            **exc.context,
        )
        if config.fail_open:
            return PlainTextResponse("allowed", status_code=200)
        return PlainTextResponse(BLOCKED_CONTENT_MESSAGE, status_code=400)

    is_blocked = flags["is_injection"] or flags["is_unsafe"]
    if is_blocked:
        log_event(
            logger,
            logging.INFO,
            "opa.security.blocked",
            "Request blocked by OPA security check",
            request_id=request_id,
            is_injection=flags["is_injection"],
            is_unsafe=flags["is_unsafe"],
            status_code=400,
        )
        VIOLATIONS_COUNTER.add(1)
        return PlainTextResponse(BLOCKED_CONTENT_MESSAGE, status_code=400)

    log_event(
        logger,
        logging.DEBUG,
        "opa.security.checked",
        "Request allowed by OPA security check",
        request_id=request_id,
        is_injection=flags["is_injection"],
        is_unsafe=flags["is_unsafe"],
        status_code=200,
    )
    return PlainTextResponse("allowed", status_code=200)


@app.post("/mask")
async def mask(http_request: Request):
    request_id = http_request.state.request_id
    client: OpaClient = http_request.app.state.opa_client
    config: OpaClientConfig = http_request.app.state.opa_config

    text = _load_body_or_400(
        await http_request.body(), config.max_body_bytes, request_id
    )

    try:
        result: MaskResult = await client.mask(text)
    except OpaUpstreamError as exc:
        log_event(
            logger,
            logging.DEBUG,
            "opa.upstream.failed",
            "OPA upstream call failed on /mask",
            request_id=request_id,
            fail_mode=config.fail_mode,
            **exc.context,
        )
        if config.fail_open:
            return PlainTextResponse(text, status_code=200)
        raise HTTPException(status_code=502, detail="OPA masking unavailable") from exc

    output_bytes = (
        len(result.value.encode("utf-8"))
        if result.is_string
        else len(json.dumps(result.value).encode("utf-8"))
    )
    pii_changed = (
        result.value != text if result.is_string else True
    )
    mask_detected = _contains_asterisk_sequence(result.value)
    log_event(
        logger,
        logging.INFO if mask_detected else logging.DEBUG,
        "opa.mask.completed",
        "PII masking completed" if mask_detected else "PII masking not detected",
        request_id=request_id,
        is_string=result.is_string,
        output_bytes=output_bytes,
        pii_changed=pii_changed,
        mask_detected=mask_detected,
        status_code=200,
    )
    if mask_detected:
        PII_MASKING_SUCCESSFUL_COUNTER.add(1)

    if result.is_string:
        return PlainTextResponse(result.value, status_code=200)
    return JSONResponse(result.value, status_code=200)
