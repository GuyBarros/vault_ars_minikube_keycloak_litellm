import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from api.routes import router
from app_logging.logger import bind_request_context, clear_request_id, configure_logging
from config.settings import settings

configure_logging(settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings.log_configured_values()
    structlog.get_logger(__name__).info(
        "consul_mcp_authz_api_starting",
        vault_addr=settings.vault_addr,
        kv_path=f"{settings.vault_kv_mount}/{settings.vault_kv_path}",
    )
    yield
    structlog.get_logger(__name__).info("consul_mcp_authz_api_stopping")


app = FastAPI(
    title="Consul MCP Authz API",
    description="REST API for managing the MCP authorization catalog stored in Vault KV v2.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    incoming = request.headers.get("X-Request-ID")
    request_id = incoming or str(uuid4())
    start = time.monotonic()
    bind_request_context(request, request_id)
    try:
        response = await call_next(request)
        duration_ms = int((time.monotonic() - start) * 1000)
        structlog.get_logger("api.access").debug(
            "request_completed",
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        if incoming is not None:
            response.headers["X-Request-ID"] = request_id
        return response
    finally:
        clear_request_id()


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    structlog.get_logger(__name__).exception("unhandled_exception", error=str(exc))
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.include_router(router)
