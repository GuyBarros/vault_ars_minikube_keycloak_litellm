import base64
import binascii
import json
import logging
import os
from time import perf_counter
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response

from pydantic import BaseModel

from pii_masking import mask_pii_text
from realtime_detections import evaluate_text_metrics
from structured_logging import (
    configure_logging,
    get_logger,
    log_event,
    reset_request_id,
    reset_request_fields,
    set_request_id,
    set_request_fields,
)
from telemetry import (
    PII_MASKING_SUCCESSFUL_COUNTER,
    VIOLATIONS_COUNTER,
    render_metrics,
)

load_dotenv()
configure_logging()
THRESHOLD = 0.5
logger = get_logger(__name__)

app = FastAPI(title="Combined Guardrails API")


class GuardRailResponse(BaseModel):
    is_blocked: bool
    filters: str
    text: str


def decode_base64_text(encoded_text: str) -> str:
    try:
        decoded_bytes = base64.b64decode(encoded_text, validate=True)
        return decoded_bytes.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise ValueError(
            "The text field must contain valid base64-encoded UTF-8."
        ) from exc


def parse_encoded_text_payload(body: bytes) -> str:
    try:
        payload = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Request body must be valid UTF-8.") from exc

    if not payload.strip():
        raise ValueError("Request body must contain a base64-encoded UTF-8 string.")

    try:
        parsed_payload = json.loads(payload)
    except json.JSONDecodeError:
        return payload.strip()

    if not isinstance(parsed_payload, str):
        raise ValueError("Request body must contain a base64-encoded UTF-8 string.")

    return parsed_payload.strip()


def guardrail_response(text: str, request_id: str, client_ip: str) -> GuardRailResponse:
    metric_scores = evaluate_text_metrics(text)
    triggered_filters = [
        metric_name
        for metric_name, metric_value in metric_scores.items()
        if metric_value > THRESHOLD
    ]

    is_blocked = any(f != "pii" for f in triggered_filters)
    if is_blocked:
        VIOLATIONS_COUNTER.add(1)

    result = GuardRailResponse(
        is_blocked=is_blocked,
        filters=",".join(triggered_filters),
        text=text,
    )
    log_event(
        logger,
        logging.DEBUG,
        "guardrails.response.generated",
        "Generated guardrail response",
        request_id=request_id,
        is_blocked=result.is_blocked,
        filters=triggered_filters,
    )
    return result


def get_client_ip(http_request: Request) -> str:
    forwarded_for = http_request.headers.get("x-forwarded-for")
    return (
        forwarded_for.split(",")[0].strip()
        if forwarded_for
        else (http_request.client.host if http_request.client else "unknown")
    )


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    if request.url.path == "/metrics":
        return await call_next(request)

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


@app.post("/evaluate")
async def evaluate(
    http_request: Request,
):
    request_id = http_request.state.request_id
    client_ip = http_request.state.client_ip
    try:
        encoded_text = parse_encoded_text_payload(await http_request.body())
        decoded_text = decode_base64_text(encoded_text)
    except ValueError as exc:
        log_event(
            logger,
            logging.WARNING,
            "guardrails.request.invalid_payload",
            "Received invalid base64 input",
            request_id=request_id,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        result = guardrail_response(
            decoded_text, request_id=request_id, client_ip=client_ip
        )
        if result.is_blocked:
            log_event(
                logger,
                logging.DEBUG,
                "guardrails.request.blocked",
                "Request blocked by guardrails",
                request_id=request_id,
                filters=result.filters.split(",") if result.filters else [],
                status_code=400,
            )
            return PlainTextResponse(
                os.getenv("BLOCKED_CONTENT_MESSAGE", "This content was blocked."),
                status_code=400,
            )
        log_event(
            logger,
            logging.DEBUG,
            "guardrails.request.allowed",
            "Request allowed by guardrails",
            request_id=request_id,
            status_code=200,
        )
        return result.text
    except RuntimeError as exc:
        logger.exception(
            "Guardrail evaluation failed",
            extra={
                "event": "guardrails.evaluation.failed",
                "request_id": request_id,
                "status_code": 502,
            },
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/mask")
async def mask(
    http_request: Request,
):
    request_id = http_request.state.request_id
    client_ip = http_request.state.client_ip

    try:
        encoded_text = parse_encoded_text_payload(await http_request.body())
        decoded_text = decode_base64_text(encoded_text)
        masked_text = mask_pii_text(decoded_text)
        pii_changed = masked_text != decoded_text
        if pii_changed:
            PII_MASKING_SUCCESSFUL_COUNTER.add(1)
        return masked_text
    except ValueError as exc:
        log_event(
            logger,
            logging.WARNING,
            "guardrails.request.invalid_payload",
            "Received invalid base64 input",
            request_id=request_id,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        logger.exception(
            "PII masking failed",
            extra={
                "event": "guardrails.mask.failed",
                "request_id": request_id,
                "status_code": 502,
            },
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
