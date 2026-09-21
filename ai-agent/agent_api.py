from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from langchain.chat_models import init_chat_model

from agent_runtime import AgentRuntime
from assurance import AssuranceTracker
from config import Settings, load_settings
from errors import AppError
from identity import OboTokenService
from logging_utils import (
    bind_log_context,
    build_uvicorn_log_config,
    log_event,
    reset_log_context,
)
from mcp_client import extract_required_scopes, fetch_mcp_tools
from models import AgentTokensResponse, AssuranceResponse, ChatRequest
from scoped_tool import make_scoped_tool
from security import (
    extract_agent_identity_claims,
    extract_bearer_token,
    extract_user_identity_claims,
    validate_access_token,
)
from tools import TOOLS as LOCAL_TOOLS

SETTINGS = load_settings()
LOGGER = logging.getLogger("agent_api")


def _get_client_ip(request: Request) -> str | None:
    return request.client.host if request.client is not None else None


def _build_base_llm(settings: Settings):
    if settings.litellm_base_url:
        return init_chat_model(
            settings.model,
            streaming=True,
            base_url=settings.litellm_base_url,
            # The OpenAI client insists on a key; LiteLLM ignores it - the
            # caller is admitted by its mesh identity (litellm-gateway/pdp_auth.py).
            api_key="sk-litellm-local",
        )
    if settings.model.startswith("ollama:") and settings.ollama_base_url:
        return init_chat_model(
            settings.model, streaming=True, base_url=settings.ollama_base_url
        )
    return init_chat_model(settings.model, streaming=True)


def _build_runtime_for_request(
    llm,
    tools: list,
) -> AgentRuntime:
    return AgentRuntime(
        llm_with_tools=llm.bind_tools(tools),
        logger=LOGGER,
        tool_registry={tool.name: tool for tool in tools},
    )


def _error_response(request: Request, status_code: int, error: str, message: str) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    log_event(
        LOGGER,
        "request_failed",
        level=logging.ERROR,
        message="Request failed",
        request_id=request_id,
        path=request.url.path,
        status_code=status_code,
        error=error,
        error_message=message,
    )
    return JSONResponse(
        status_code=status_code,
        content={"error": error, "message": message},
    )


async def _discover_mcp_template_tools_at_startup(
    user_mcp_url: str,
    timeout_seconds: float = 30.0,
    max_attempts: int = 5,
    initial_backoff_seconds: float = 2.0,
) -> list:
    """Discovery against user-mcp using no OBO, retried with backoff.

    user-mcp must be configured with `USER_MCP_ALLOW_UNAUTH_DISCOVERY=true` for
    this to succeed; the network channel is presumed secured at the mesh layer
    (Consul service-intentions). The returned `_meta.required_scopes` on each
    template drives the per-call scope the agent later exchanges OBO tokens
    for.

    Retries exist because a rolling deploy routinely starts ai-agent and
    user-mcp (plus its mesh sidecars) within moments of each other, with no
    ordering guarantee — a single attempt can lose that race and land the
    agent with zero tools for its whole lifetime, since discovery only runs
    once at startup. All attempts failing is still swallowed so the service
    boots regardless — `query_agent` will operate with no MCP tools until a
    restart succeeds.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            templates = await fetch_mcp_tools(
                user_mcp_url,
                None,
                "startup",
                timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - startup discovery is best-effort
            if attempt == max_attempts:
                log_event(
                    LOGGER,
                    "mcp_discovery_failed_at_startup",
                    level=logging.WARNING,
                    message=(
                        f"MCP startup discovery failed against {user_mcp_url} "
                        f"after {max_attempts} attempts: {exc}"
                    ),
                    user_mcp_url=user_mcp_url,
                    attempt=attempt,
                )
                return []
            backoff = initial_backoff_seconds * (2 ** (attempt - 1))
            log_event(
                LOGGER,
                "mcp_discovery_attempt_failed",
                level=logging.INFO,
                message=(
                    f"MCP startup discovery attempt {attempt}/{max_attempts} failed "
                    f"against {user_mcp_url}: {exc}. Retrying in {backoff}s."
                ),
                user_mcp_url=user_mcp_url,
                attempt=attempt,
            )
            await asyncio.sleep(backoff)
            continue

        templates = [
            tool
            for tool in templates
            if getattr(tool, "name", None) != "delete_user_by_email"
        ]
        log_event(
            LOGGER,
            "mcp_discovery_completed_at_startup",
            message=f"Discovered {len(templates)} MCP tool template(s) at startup",
            user_mcp_url=user_mcp_url,
            tool_count=len(templates),
            tool_names=[getattr(t, "name", None) for t in templates],
            attempt=attempt,
        )
        return templates


def _wrap_mcp_tools_with_per_call_obo(
    template_tools: list,
    token_service: OboTokenService,
    subject_token: str | None,
    request_id: str,
    user_mcp_url: str,
    bypass: bool,
    assurance_tracker: AssuranceTracker | None = None,
    tool_call_timeout_seconds: float = 30.0,
) -> list:
    """Replace each MCP tool with a wrapper that exchanges a scope-specific
    OBO right before the upstream call. delete_user_by_email is never bound.
    In bypass mode (no real user), pass the remaining templates through
    unchanged so the dev loop keeps working."""
    usable = [tool for tool in template_tools if getattr(tool, "name", None) != "delete_user_by_email"]
    if len(usable) != len(template_tools):
        log_event(
            LOGGER,
            "mcp_tool_disabled",
            level=logging.WARNING,
            message="delete_user_by_email is disabled and is not bound to the agent.",
            request_id=request_id,
            tool_name="delete_user_by_email",
        )
    if bypass or subject_token is None:
        return usable

    wrapped: list = []
    for template in usable:
        required_scopes = extract_required_scopes(template)
        if not required_scopes:
            log_event(
                LOGGER,
                "mcp_tool_missing_required_scopes",
                level=logging.WARNING,
                message=(
                    f"MCP tool {getattr(template, 'name', '?')} did not advertise "
                    f"_meta.required_scopes; passing through without per-call OBO."
                ),
                request_id=request_id,
                tool_name=getattr(template, "name", None),
            )
            wrapped.append(template)
            continue
        wrapped.append(
            make_scoped_tool(
                template_tool=template,
                required_scopes=required_scopes,
                token_service=token_service,
                subject_token=subject_token,
                request_id=request_id,
                user_mcp_url=user_mcp_url,
                assurance_tracker=assurance_tracker,
                tool_call_timeout_seconds=tool_call_timeout_seconds,
            )
        )
    return wrapped


def _load_startup_agent_id(token_service: OboTokenService) -> str | None:
    try:
        actor_token = token_service.read_actor_token()
    except AppError as exc:
        if exc.error != "actor_token_expired":
            log_event(
                LOGGER,
                "actor_token_unavailable_at_startup",
                level=logging.WARNING,
                message="Actor token unavailable at startup; agent_id will be omitted from log prefix.",
                error=exc.error,
                error_message=exc.message,
            )
        return None
    return extract_agent_identity_claims(actor_token)["actor_agent_id"]


def create_app(
    settings: Settings | None = None,
    runtime: AgentRuntime | None = None,
    token_service: OboTokenService | None = None,
    llm: object | None = None,
) -> FastAPI:
    active_settings = settings or SETTINGS

    @asynccontextmanager
    async def lifespan(app_inner: FastAPI):
        # Do not await discovery before yield: uvicorn only binds :8000 after
        # lifespan startup finishes, and a 30s MCP retry loop would 503 every
        # caller (web -> LiteLLM -> this pod) with Envoy "connection refused".
        # Queries that land before discovery completes run with no MCP tools.
        async def _discover() -> None:
            app_inner.state.mcp_template_tools = (
                await _discover_mcp_template_tools_at_startup(
                    active_settings.user_mcp_url,
                    active_settings.mcp_tool_call_timeout_seconds,
                )
            )

        app_inner.state.mcp_discovery_task = asyncio.create_task(_discover())
        yield
        app_inner.state.mcp_discovery_task.cancel()
        try:
            await app_inner.state.mcp_discovery_task
        except asyncio.CancelledError:
            pass

    app = FastAPI(lifespan=lifespan)
    app.state.settings = active_settings
    app.state.llm = llm or _build_base_llm(active_settings)
    # When set (in tests), this fixed runtime is used instead of building one
    # per request. In production it stays None and the route reuses the
    # startup-discovered MCP tool templates, wrapping them per request with
    # tool-specific OBO tokens.
    app.state.agent_runtime = runtime
    # Populated by the lifespan handler at startup. Kept as an empty list so
    # the route is callable even before lifespan runs (test contexts).
    app.state.mcp_template_tools = []
    app.state.token_service = token_service or OboTokenService(
        settings=active_settings,
        logger=LOGGER,
    )
    app.state.assurance_tracker = AssuranceTracker()
    app.state.actor_agent_id = _load_startup_agent_id(app.state.token_service)

    @app.middleware("http")
    async def request_logging_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        request.state.client_ip = _get_client_ip(request)
        context_token = bind_log_context(
            request_id=request_id,
            path=request.url.path,
            http_method=request.method,
            client_ip=request.state.client_ip,
            actor_agent_id=request.app.state.actor_agent_id,
        )
        try:
            log_event(
                LOGGER,
                "request_received",
                level=logging.DEBUG,
                message="Request received",
                request_id=request_id,
                path=request.url.path,
                method=request.method,
            )
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            if request.url.path != "/v1/agent/query" and not isinstance(
                response, StreamingResponse
            ):
                log_event(
                    LOGGER,
                    "response_sent",
                    level=logging.DEBUG,
                    message="Response sent",
                    request_id=request_id,
                    path=request.url.path,
                    status_code=response.status_code,
                )
            return response
        finally:
            reset_log_context(context_token)

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return _error_response(request, exc.status_code, exc.error, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _error_response(
            request,
            status_code=400,
            error="invalid_request",
            message=str(exc),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        return _error_response(
            request,
            status_code=500,
            error="agent_error",
            message="Agent execution failed.",
        )

    @app.post("/v1/agent/query")
    async def query_agent(request: Request, chat_request: ChatRequest):
        access_token: str | None = None
        preferred_username: str | None = None
        if not request.app.state.settings.bypass_auth_token_exchange:
            access_token = extract_bearer_token(request)
            access_token_payload = validate_access_token(access_token)
            preferred_username = extract_user_identity_claims(
                access_token_payload
            )["preferred_username"]
            # Actor JWT is independent of the chat subject. Re-read and check
            # exp on every turn so a rotated/expired Vault token fails here,
            # not on the first tools/call.
            request.app.state.token_service.read_actor_token()
        bind_log_context(preferred_username=preferred_username)

        if access_token is not None and not await asyncio.to_thread(
            request.app.state.token_service.subject_is_active, access_token, request.state.request_id
        ):
            raise AppError(
                status_code=401,
                error="invalid_token",
                message="Bearer token is no longer active (it was revoked, or its session has ended).",
            )

        # A step-up prompt belongs to the query that hit it, not to later ones.
        tracker = request.app.state.assurance_tracker
        last = tracker.get_last(access_token) if access_token else None
        if last and last.get("decision") == "STEP_UP_REQUIRED":
            tracker.discard(access_token)

        runtime = request.app.state.agent_runtime
        if runtime is None:
            scoped_tools = _wrap_mcp_tools_with_per_call_obo(
                template_tools=request.app.state.mcp_template_tools,
                token_service=request.app.state.token_service,
                subject_token=access_token,
                request_id=request.state.request_id,
                user_mcp_url=request.app.state.settings.user_mcp_url,
                bypass=request.app.state.settings.bypass_auth_token_exchange,
                assurance_tracker=request.app.state.assurance_tracker,
                tool_call_timeout_seconds=request.app.state.settings.mcp_tool_call_timeout_seconds,
            )
            tools = list(LOCAL_TOOLS) + scoped_tools
            runtime = _build_runtime_for_request(request.app.state.llm, tools)

        return await runtime.handle_request(
            chat_request=chat_request,
            request_id=request.state.request_id,
            request_path=request.url.path,
            request_method=request.method,
            client_ip=request.state.client_ip,
        )

    @app.get("/v1/agent/tokens", response_model=AgentTokensResponse)
    async def get_cached_tokens(request: Request) -> AgentTokensResponse:
        # actor_token is independent of OBO exchange and is always available.
        # The OBO returned here is the most recently exchanged one for this
        # subject — when a turn issues multiple scoped OBOs (read then write),
        # the inspector reflects whichever was last requested.
        actor_token = request.app.state.token_service.read_actor_token()

        if request.app.state.settings.bypass_auth_token_exchange:
            return AgentTokensResponse(actor_token=actor_token, obo_token=None)

        access_token = extract_bearer_token(request)
        validate_access_token(access_token)
        obo_token = request.app.state.token_service.get_last_obo_token(access_token)
        return AgentTokensResponse(actor_token=actor_token, obo_token=obo_token)

    @app.get("/v1/agent/assurance", response_model=AssuranceResponse)
    async def get_last_assurance(request: Request) -> AssuranceResponse:
        # Scoped tools (and therefore assurance tracking) don't run in
        # bypass mode - see _wrap_mcp_tools_with_per_call_obo's early return.
        if request.app.state.settings.bypass_auth_token_exchange:
            return AssuranceResponse()

        access_token = extract_bearer_token(request)
        validate_access_token(access_token)
        assurance = request.app.state.assurance_tracker.get_last(access_token)
        if assurance is None:
            return AssuranceResponse()
        return AssuranceResponse(**assurance)

    # A2A agent card, for LiteLLM's Agents > Discovery. Public metadata only
    # (no tokens); the mesh already limits who can reach this service. A2A
    # clients look in different places depending on spec version, so serve all.
    @app.get("/.well-known/agent-card.json")
    @app.get("/.well-known/agent.json")
    @app.get("/agent.json")
    async def agent_card(request: Request) -> dict:
        return {
            "name": "ai-agent",
            "description": "Governed user-management agent (OBO + CIBA). Chat via /v1/agent.",
            "url": str(request.base_url).rstrip("/"),
            "protocolVersion": "1.0",
            "version": "1.0.0",
            "capabilities": {},
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text"],
            "skills": [],
        }

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=SETTINGS.host,
        port=SETTINGS.port,
        log_level=SETTINGS.log_level.lower(),
        log_config=build_uvicorn_log_config(SETTINGS.log_level),
    )
