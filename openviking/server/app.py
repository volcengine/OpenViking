# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""FastAPI application for OpenViking HTTP Server."""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from openviking.server.api_keys import APIKeyManager
from openviking.server.config import (
    ServerConfig,
    load_bot_gateway_token,
    load_server_config,
    validate_server_config,
)
from openviking.server.dependencies import set_service
from openviking.server.error_mapping import map_exception
from openviking.server.identity import AuthMode, Role
from openviking.server.models import ERROR_CODE_TO_HTTP_STATUS, ErrorInfo, Response
from openviking.server.routers import (
    admin_router,
    bot_router,
    console_router,
    content_router,
    debug_router,
    filesystem_router,
    metrics_router,
    observer_router,
    pack_router,
    privacy_configs_router,
    relations_router,
    resources_router,
    search_router,
    sessions_router,
    stats_router,
    system_router,
    tasks_router,
    webdav_router,
)
from openviking.service.core import OpenVikingService
from openviking.service.task_tracker import get_task_tracker
from openviking_cli.exceptions import OpenVikingError
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.logger import init_otel_log_handler_from_server_config

logger = get_logger(__name__)


def _on_deferred_init_done(task):
    if task.cancelled():
        logger.warning("Deferred initialization cancelled")
        return

    exc = task.exception()
    if exc is None:
        return

    logger.error(
        "Deferred initialization failed, exiting",
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    os._exit(1)


def _format_error_location(loc: object) -> str:
    if not isinstance(loc, (list, tuple)):
        return "request"
    parts = [str(part) for part in loc if part is not None]
    return ".".join(parts) if parts else "request"


def _normalize_validation_error(error: object) -> dict:
    if not isinstance(error, dict):
        return {"loc": ["request"], "message": str(error), "type": "value_error"}
    loc = error.get("loc", ["request"])
    if not isinstance(loc, (list, tuple)):
        loc = [loc]
    return {
        "loc": [str(part) for part in loc],
        "message": str(error.get("msg") or "Invalid value"),
        "type": str(error.get("type") or "value_error"),
    }


def _validation_error_message(errors: list[dict]) -> str:
    if not errors:
        return "Invalid request parameters"
    first = errors[0]
    location = _format_error_location(first.get("loc"))
    message = first.get("message") or "Invalid value"
    return f"Invalid request parameters: {location}: {message}"


_FRAMEWORK_HTTP_STATUS_TO_ERROR_CODE = {
    400: "INVALID_ARGUMENT",
    401: "UNAUTHENTICATED",
    403: "PERMISSION_DENIED",
    404: "NOT_FOUND",
    409: "CONFLICT",
    422: "INVALID_ARGUMENT",
    429: "RESOURCE_EXHAUSTED",
    502: "UNAVAILABLE",
    503: "UNAVAILABLE",
    504: "DEADLINE_EXCEEDED",
}


def _error_code_from_framework_http_status(status_code: int) -> str:
    """Best-effort envelope code for framework/proxy HTTPException fallbacks.

    Business routes should raise OpenVikingError subclasses directly instead
    of relying on this status-code conversion.
    """
    if status_code in _FRAMEWORK_HTTP_STATUS_TO_ERROR_CODE:
        return _FRAMEWORK_HTTP_STATUS_TO_ERROR_CODE[status_code]
    return "INTERNAL" if status_code >= 500 else "UNKNOWN"


def _message_from_http_detail(detail: object) -> str:
    if isinstance(detail, str) and detail:
        return detail
    if isinstance(detail, list):
        errors = [_normalize_validation_error(item) for item in detail]
        return _validation_error_message(errors)
    if isinstance(detail, dict):
        for key in ("message", "detail", "error"):
            value = detail.get(key)
            if isinstance(value, str) and value:
                return value
    if detail:
        return str(detail)
    return "HTTP request failed"


def create_app(
    config: Optional[ServerConfig] = None,
    service: Optional[OpenVikingService] = None,
) -> FastAPI:
    """Create FastAPI application.

    Args:
        config: Server configuration. If None, loads from default location.
        service: Pre-initialized OpenVikingService (optional).

    Returns:
        FastAPI application instance
    """
    if config is None:
        config = load_server_config()

    validate_server_config(config)

    async def _deferred_init(service, app, config):
        """Run heavy initialization in background after server starts accepting requests."""
        await service.initialize()

        # Initialize APIKeyManager after service (needs VikingFS)
        effective_auth_mode = config.get_effective_auth_mode()
        if config.root_api_key and config.root_api_key != "":
            api_key_manager = APIKeyManager(
                root_key=config.root_api_key,
                viking_fs=service.viking_fs,
                api_key_hashing_enabled=config.api_key_hashing_enabled,
            )
            await api_key_manager.load()
            app.state.api_key_manager = api_key_manager
            logger.info(
                "APIKeyManager initialized with api_key_hashing_enabled=%s",
                config.api_key_hashing_enabled,
            )
        elif effective_auth_mode == AuthMode.TRUSTED:
            app.state.api_key_manager = None
            if config.root_api_key and config.root_api_key != "":
                logger.info(
                    "Trusted mode enabled: authentication trusts X-OpenViking-Account/User/Agent "
                    "headers and requires the configured server API key on each request. "
                    "Only expose this server behind a trusted network boundary or "
                    "identity-injecting gateway."
                )
            else:
                logger.warning(
                    "Trusted mode enabled: authentication uses X-OpenViking-Account/User/Agent "
                    "headers without API keys. This is only allowed on localhost. "
                    "Only expose this server behind a trusted network boundary or "
                    "identity-injecting gateway after configuring server.root_api_key."
                )
        else:
            # AuthMode.DEV - logging already handled in validate_server_config
            app.state.api_key_manager = None

        logger.info("OpenVikingService initialization complete")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Application lifespan handler."""
        nonlocal service
        owns_service = service is None
        if owns_service:
            service = OpenVikingService()

        assert service is not None
        set_service(service)

        from openviking.metrics.global_api import (
            init_metrics_from_server_config,
        )
        from openviking.observability.usage_audit import init_usage_audit_from_server_config

        init_metrics_from_server_config(config, app=app, service=service)
        if config.observability.metrics.enabled:
            logger.info("Prometheus metrics enabled at /metrics")
        await init_usage_audit_from_server_config(config, app=app, service=service)

        # Initialize OAuth 2.1 store + provider when enabled in OpenViking config.
        # The store + provider instances were already constructed at app
        # creation time so the SDK routes could capture them; here we just
        # async-initialize the SQLite connection on the same instance.
        oauth_store = getattr(app.state, "oauth_store", None)
        oauth_gc_task: Optional[asyncio.Task] = None
        if oauth_store is not None:
            await oauth_store.initialize()

            async def _oauth_gc_loop(store) -> None:  # noqa: ANN001
                while True:
                    try:
                        await asyncio.sleep(60)
                        await store.gc_expired()
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:  # noqa: BLE001
                        logger.warning("OAuth GC loop error: %s", e)

            oauth_gc_task = asyncio.create_task(_oauth_gc_loop(oauth_store))
            app.state.oauth_gc_task = oauth_gc_task
            logger.info("OAuth 2.1 store initialized at %s", oauth_store._db_path)

        # Start TaskTracker cleanup loop
        task_tracker = get_task_tracker()
        task_tracker.start_cleanup_loop()

        # Initialize tracing and OTLP log export from server.observability.
        from openviking.telemetry import tracer_module

        tracer_module.init_tracer_from_server_config(config)
        init_otel_log_handler_from_server_config(config)

        # Start MCP session manager (must be active before /mcp requests)
        from openviking.server.mcp_endpoint import mcp_lifespan

        deferred_task = None

        async with mcp_lifespan():
            # Start heavy initialization as background task after yield
            if service is not None:
                deferred_task = asyncio.create_task(_deferred_init(service, app, config))
                deferred_task.add_done_callback(_on_deferred_init_done)
            yield

        # Wait for deferred initialization to complete before shutdown
        if deferred_task and not deferred_task.done():
            try:
                await deferred_task
            except Exception:
                logger.exception("Deferred initialization failed")

        # Cleanup
        from openviking.metrics.global_api import shutdown_metrics_async
        from openviking.observability.usage_audit import shutdown_usage_audit

        await shutdown_usage_audit(app=app)
        await shutdown_metrics_async(app=app)
        task_tracker.stop_cleanup_loop()
        if oauth_gc_task is not None:
            oauth_gc_task.cancel()
            try:
                await oauth_gc_task
            except (asyncio.CancelledError, Exception):
                pass
        oauth_store_state = getattr(app.state, "oauth_store", None)
        if oauth_store_state is not None:
            try:
                await oauth_store_state.close()
            except Exception as e:  # noqa: BLE001
                logger.warning("OAuth store close failed: %s", e)
        if owns_service and service:
            try:
                await service.close()
                logger.info("OpenVikingService closed")
            except asyncio.CancelledError as e:
                logger.warning(f"OpenVikingService close cancelled during shutdown: {e}")
            except Exception as e:
                logger.warning(f"OpenVikingService close failed during shutdown: {e}")

    app = FastAPI(
        title="OpenViking API",
        description="OpenViking HTTP Server - Agent-native context database",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.state.config = config

    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Body dump middleware must be registered BEFORE observability so it ends up
    # nested inside the trace span (in Starlette, middleware added later wraps
    # earlier-added ones — so earlier registration = inner layer).
    if config.observability.dump_body.enabled:
        from openviking.server.body_dump_middleware import (
            create_dump_http_body_middleware,
        )

        _dump_body_fn = create_dump_http_body_middleware(
            max_bytes=config.observability.dump_body.max_bytes,
        )

        @app.middleware("http")
        async def dump_http_body(request: Request, call_next: Callable):
            return await _dump_body_fn(request, call_next)

        logger.info(
            "HTTP body dump middleware enabled (max_bytes=%d) — bodies will be "
            "attached to trace spans. Disable in production via "
            "server.observability.dump_body.enabled=false.",
            config.observability.dump_body.max_bytes,
        )

    # Add HTTP observability middleware (metrics, tracing).
    # Note: In FastAPI/Starlette, middleware added later executes first (outer layer).
    # We want timing to be the outermost layer to measure the full request duration.
    from openviking.observability.http_observability_middleware import (
        create_http_observability_middleware,
    )

    http_observability_middleware = create_http_observability_middleware()

    @app.middleware("http")
    async def add_http_observability(request: Request, call_next: Callable):
        return await http_observability_middleware(request, call_next)

    # Add request timing middleware last (so it executes first as the outermost layer)
    # This ensures X-Process-Time includes the full request duration including
    # observability middleware overhead.
    # Add request header logging middleware (for debug)
    @app.middleware("http")
    async def log_request_headers(request: Request, call_next: Callable):
        access_logger = logging.getLogger("uvicorn.access")
        if access_logger.isEnabledFor(logging.DEBUG):
            headers = dict(request.headers)
            header_names = ", ".join(sorted(headers.keys()))
            access_logger.debug(
                f"Request headers for {request.method} {request.url.path}: {header_names}"
            )
        response = await call_next(request)
        return response

    # Add request timing middleware
    @app.middleware("http")
    async def add_timing(request: Request, call_next: Callable):
        """
        Middleware to measure request processing time.

        This middleware is added last so it executes as the outermost layer,
        ensuring X-Process-Time includes the full request duration including
        all other middleware overhead.

        Args:
            request: The incoming HTTP request.
            call_next: The next middleware/handler in the chain.

        Returns:
            The response with X-Process-Time header added.
        """
        start_time = time.perf_counter()
        response = await call_next(request)
        process_time = time.perf_counter() - start_time
        response.headers["X-Process-Time"] = str(process_time)
        return response

    # Add exception handler for OpenVikingError
    @app.exception_handler(OpenVikingError)
    async def openviking_error_handler(request: Request, exc: OpenVikingError):
        http_status = ERROR_CODE_TO_HTTP_STATUS.get(exc.code, 500)
        return JSONResponse(
            status_code=http_status,
            content=Response(
                status="error",
                error=ErrorInfo(
                    code=exc.code,
                    message=exc.message,
                    details=exc.details,
                ),
            ).model_dump(),
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(request: Request, exc: RequestValidationError):
        errors = [_normalize_validation_error(error) for error in exc.errors()]
        code = "INVALID_ARGUMENT"
        return JSONResponse(
            status_code=ERROR_CODE_TO_HTTP_STATUS[code],
            content=Response(
                status="error",
                error=ErrorInfo(
                    code=code,
                    message=_validation_error_message(errors),
                    details={"validation_errors": errors},
                ),
            ).model_dump(exclude_none=True),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        code = _error_code_from_framework_http_status(exc.status_code)
        response_status = exc.status_code
        if code != "UNKNOWN":
            response_status = ERROR_CODE_TO_HTTP_STATUS.get(code, exc.status_code)
        details = None
        if exc.status_code != response_status:
            details = {"original_http_status_code": exc.status_code}
        return JSONResponse(
            status_code=response_status,
            headers=exc.headers,
            content=Response(
                status="error",
                error=ErrorInfo(
                    code=code,
                    message=_message_from_http_detail(exc.detail),
                    details=details,
                ),
            ).model_dump(exclude_none=True),
        )

    # Catch-all for unhandled exceptions so clients always get JSON
    @app.exception_handler(Exception)
    async def general_error_handler(request: Request, exc: Exception):
        mapped = map_exception(exc)
        if mapped is not None:
            http_status = ERROR_CODE_TO_HTTP_STATUS.get(mapped.code, 500)
            logger.warning(
                "Mapped unhandled exception to structured API error",
                extra={"error_code": mapped.code, "error_message": mapped.message},
                exc_info=exc,
            )
            return JSONResponse(
                status_code=http_status,
                content=Response(
                    status="error",
                    error=ErrorInfo(
                        code=mapped.code,
                        message=mapped.message,
                        details=mapped.details,
                    ),
                ).model_dump(),
            )

        logger.exception("Unhandled exception")
        return JSONResponse(
            status_code=500,
            content=Response(
                status="error",
                error=ErrorInfo(
                    code="INTERNAL",
                    message="Internal server error",
                ),
            ).model_dump(),
        )

    # Configure Bot API if --with-bot is enabled
    if config.with_bot:
        import openviking.server.routers.bot as bot_module

        bot_module.set_bot_api_url(config.bot_api_url)
        bot_module.set_bot_api_key(load_bot_gateway_token())
        logger.info(f"Bot API proxy enabled, forwarding to {config.bot_api_url}")
    else:
        logger.info("Bot API proxy disabled (use --with-bot to enable)")

    # Register routers
    app.include_router(system_router)
    app.include_router(admin_router)
    app.include_router(resources_router)
    app.include_router(filesystem_router)
    app.include_router(content_router)
    app.include_router(console_router)
    app.include_router(search_router)
    app.include_router(relations_router)
    app.include_router(privacy_configs_router)
    app.include_router(sessions_router)
    app.include_router(stats_router)
    app.include_router(pack_router)
    app.include_router(debug_router)
    app.include_router(observer_router)
    app.include_router(metrics_router)
    app.include_router(tasks_router)
    app.include_router(webdav_router)
    app.include_router(bot_router, prefix="/bot/v1")

    # OAuth 2.1: when enabled, mount the official MCP SDK auth routes
    # (DCR / authorize / token / metadata) plus our authorize page + OTP
    # issuance endpoint. The Provider that backs the SDK routes is built
    # in the lifespan; here we only register the route handlers, since the
    # SDK routes inspect request.app.state at call time.
    try:
        ov_cfg = get_openviking_config()
        if ov_cfg.oauth.enabled:
            from mcp.server.auth.routes import create_auth_routes
            from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
            from pydantic import AnyHttpUrl

            from openviking.server.oauth.router import router as oauth_router

            # Custom routes (authorize page + /api/v1/auth/otp).
            app.include_router(oauth_router)

            # SDK-owned routes (DCR / authorize / token / metadata / revoke).
            # We need a live Provider here; create_auth_routes captures it by
            # reference. Re-build the same construction the lifespan does so
            # the routes work as soon as they're hit (lifespan re-binds the
            # same instance to app.state for the OTP / authorize-page path).
            from pathlib import Path as _Path

            from openviking.server.oauth.provider import OpenVikingOAuthProvider
            from openviking.server.oauth.storage import OAuthStore

            _workspace = _Path(ov_cfg.storage.workspace).expanduser().resolve()
            _workspace.mkdir(parents=True, exist_ok=True)
            _route_store = OAuthStore(_workspace / ov_cfg.oauth.db_filename)
            # Resolution order for the AS issuer URL:
            #   1. OPENVIKING_PUBLIC_BASE_URL env var (deployment override)
            #   2. oauth.issuer in ov.conf (operator config)
            #   3. http://127.0.0.1:1933 (dev default; SDK accepts loopback http)
            import os as _os

            _route_issuer = (
                _os.environ.get("OPENVIKING_PUBLIC_BASE_URL", "").strip().rstrip("/")
                or ov_cfg.oauth.issuer
                or "http://127.0.0.1:1933"
            )

            # Late-binding role resolver: app.state.api_key_manager is wired
            # during lifespan, after the provider is constructed. Lambda
            # closes over `app` and looks up at call time.
            def _current_role(account_id: str, user_id: str) -> Role:
                mgr = getattr(app.state, "api_key_manager", None)
                if mgr is None or not hasattr(mgr, "get_user_role"):
                    return Role.USER
                return mgr.get_user_role(account_id, user_id)

            _route_provider = OpenVikingOAuthProvider(
                store=_route_store,
                issuer=_route_issuer,
                access_token_ttl_seconds=ov_cfg.oauth.access_token_ttl_seconds,
                refresh_token_ttl_seconds=ov_cfg.oauth.refresh_token_ttl_seconds,
                auth_code_ttl_seconds=ov_cfg.oauth.auth_code_ttl_seconds,
                role_resolver=_current_role,
            )
            # Stash the route-time instances; the lifespan replaces these with
            # initialized copies before the first request lands.
            app.state.oauth_store = _route_store
            app.state.oauth_provider = _route_provider

            sdk_routes = create_auth_routes(
                provider=_route_provider,
                issuer_url=AnyHttpUrl(_route_issuer),
                client_registration_options=ClientRegistrationOptions(enabled=True),
                revocation_options=RevocationOptions(enabled=True),
            )
            app.routes.extend(sdk_routes)
            app.state.oauth_config = ov_cfg.oauth
            logger.info(
                "OAuth 2.1 routes mounted (SDK + authorize-page + otp): %s",
                [r.path for r in sdk_routes],
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("Skipping OAuth router registration: %s", e)

    # Favicon: shared with the console static assets so 1933/console use the same logo.
    _static_dir = Path(__file__).resolve().parent.parent / "console" / "static"
    _favicon_headers = {"Cache-Control": "public, max-age=86400"}
    _favicon_files = {
        "/favicon.ico": ("favicon.ico", "image/x-icon"),
        "/favicon.png": ("favicon-32.png", "image/png"),
        "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
    }

    def _make_favicon_handler(filename: str, media_type: str):
        path = _static_dir / filename

        async def _handler():
            return FileResponse(path, media_type=media_type, headers=_favicon_headers)

        return _handler

    for _route, (_fname, _mime) in _favicon_files.items():
        app.add_api_route(_route, _make_favicon_handler(_fname, _mime), include_in_schema=False)

    # MCP endpoint — serves 5 tools (search, read, store, forget, health)
    # via streamable HTTP for Claude Code and other MCP clients.
    from starlette.routing import Route

    from openviking.server.mcp_endpoint import create_mcp_app

    app.routes.append(Route("/mcp", endpoint=create_mcp_app(), methods=["GET", "POST", "DELETE"]))

    return app
