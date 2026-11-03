# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""FastAPI application for OpenViking HTTP Server."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.exceptions import ExceptionMiddleware

from openviking.observability.http_error_context import capture_public_http_error
from openviking.server.config import (
    ServerConfig,
    load_bot_gateway_token,
    load_server_config,
    validate_server_config,
)
from openviking.server.dependencies import set_server_config
from openviking.server.error_mapping import map_exception
from openviking.server.gateway_host import ScopedRoute, create_embedded_gateway
from openviking.server.identity import Role
from openviking.server.models import ERROR_CODE_TO_HTTP_STATUS, ErrorInfo, Response
from openviking.server.profile_middleware import ProfileMiddleware
from openviking.server.request_id import REQUEST_ID_HEADER, RequestIdMiddleware
from openviking.server.routers import (
    acl_router,
    admin_router,
    agent_evolution_router,
    bot_router,
    bot_studio_router,
    compile_router,
    console_router,
    content_router,
    debug_router,
    filesystem_router,
    mcps_router,
    metrics_router,
    observer_router,
    openviking_assets_router,
    pack_router,
    privacy_configs_router,
    resources_router,
    rules_router,
    search_router,
    sessions_router,
    skills_router,
    snapshot_router,
    stats_router,
    system_router,
    tasks_router,
    user_settings_router,
    watches_router,
    webdav_router,
)
from openviking.server.runtime import ServerRuntime
from openviking.server.timing_middleware import RequestTimingMiddleware
from openviking.service.core import OpenVikingService
from openviking_cli.exceptions import OpenVikingError
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import (
    DEFAULT_OV_CONF,
    OPENVIKING_CONFIG_ENV,
    get_openviking_config,
    resolve_config_path,
)
from openviking_cli.utils.logger import configure_server_logging
from openviking_gateway.config import MOUNT_PATH as GATEWAY_MOUNT_PATH

logger = get_logger(__name__)

WORKER_HOST_ENV = "OPENVIKING_WORKER_HOST"
WORKER_PORT_ENV = "OPENVIKING_WORKER_PORT"
WORKER_WITH_BOT_ENV = "OPENVIKING_WORKER_WITH_BOT"
WORKER_BOT_API_URL_ENV = "OPENVIKING_WORKER_BOT_API_URL"
WORKER_ROLES_ENV = "OPENVIKING_WORKER_ROLES"


def _configure_default_executor(config: ServerConfig) -> None:
    """Apply the configured asyncio default executor to the current worker loop.

    The event loop owns the executor after ``set_default_executor`` and shuts it
    down when the loop closes. This must run before service initialization,
    because initialization itself can submit work through ``asyncio.to_thread``.
    """
    max_workers = config.executor_threads
    if max_workers == 0:
        return

    executor = ThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix="openviking-asyncio",
    )
    asyncio.get_running_loop().set_default_executor(executor)
    logger.info("Configured asyncio default executor: max_workers=%d", max_workers)


def create_worker_app() -> FastAPI:
    """Load file config and replay parent-process Bot CLI overrides."""
    configure_server_logging(bootstrap=True)
    resolved_config_path = resolve_config_path(
        None,
        OPENVIKING_CONFIG_ENV,
        DEFAULT_OV_CONF,
    )
    config_path = str(resolved_config_path) if resolved_config_path is not None else None
    config = load_server_config(config_path)
    configure_server_logging()
    with_bot = os.environ.get(WORKER_WITH_BOT_ENV)
    if with_bot is not None:
        config.with_bot = with_bot == "1"
    bot_api_url = os.environ.get(WORKER_BOT_API_URL_ENV)
    if bot_api_url is not None:
        config.bot_api_url = bot_api_url
    if WORKER_HOST_ENV in os.environ:
        config.host = os.environ[WORKER_HOST_ENV] or None
    if WORKER_PORT_ENV in os.environ:
        config.port = int(os.environ[WORKER_PORT_ENV])
    if WORKER_ROLES_ENV in os.environ:
        config.roles = ServerConfig(roles=os.environ[WORKER_ROLES_ENV].split(",")).roles
    return create_app(config, config_path=config_path)


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
    if location == "body" and first.get("type") == "value_error":
        # Model validators describe the constraint across fields in their own message.
        return message.removeprefix("Value error, ")
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
    config_path: Optional[str] = None,
    server_overrides: Optional[dict] = None,
) -> FastAPI:
    """Create FastAPI application.

    Args:
        config: Server configuration. If None, loads from default location.
        service: Optional prepared service for tests; the caller owns its storage.
        config_path: Resolved ov.conf path used for startup configuration.
        server_overrides: CLI arguments reapplied when loading the startup file.

    Returns:
        FastAPI application instance
    """
    resolved_config_path = (
        resolve_config_path(config_path, OPENVIKING_CONFIG_ENV, DEFAULT_OV_CONF)
        if config_path is not None or config is None
        else None
    )
    if config is None:
        config = load_server_config(
            str(resolved_config_path) if resolved_config_path is not None else config_path
        )

    validate_server_config(config)
    config = config.model_copy(deep=True)
    api_enabled = "api" in config.roles
    bot_gateway_token = load_bot_gateway_token() if config.with_bot else ""
    owns_service = service is None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Application lifespan handler."""
        nonlocal service
        _configure_default_executor(config)
        if owns_service:
            service = OpenVikingService()
        assert service is not None
        if service._initialized:
            raise RuntimeError("An injected service must not have background execution running")
        runtime = ServerRuntime(app, service, config, owns_service=owns_service)
        app.state.server_runtime = runtime
        try:
            await runtime.start(bot_gateway_token)
            yield
        finally:
            await runtime.close()

    app = FastAPI(
        title="OpenViking API",
        description="OpenViking HTTP Server - Agent-native context database",
        version="0.1.0",
        lifespan=lifespan,
        openapi_url="/openapi.json" if api_enabled else None,
        docs_url="/docs" if api_enabled else None,
        redoc_url="/redoc" if api_enabled else None,
    )

    app.state.config = config
    app.state.service_name = get_openviking_config().service_name
    app.state.server_config_overrides = server_overrides or {}
    from openviking.server.restart import RestartController

    app.state.restart_controller = RestartController()
    app.state.api_key_manager = None
    app.state.deletion_service = None
    app.state.gateway_app = None
    set_server_config(config)

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
            if request.url.path.startswith(GATEWAY_MOUNT_PATH + "/"):
                return await call_next(request)
            return await _dump_body_fn(request, call_next)

        logger.info(
            "HTTP body dump middleware enabled (max_bytes=%d) — bodies will be "
            "attached to trace spans. Disable in production via "
            "server.observability.dump_body.enabled=false.",
            config.observability.dump_body.max_bytes,
        )

    # Later registrations wrap earlier ones: timing/header logging -> profile ->
    # observability -> optional body dump -> routes. Native ASGI middleware keeps
    # response streams and request execution on the downstream application's path.
    from openviking.observability.http_observability_middleware import (
        HTTPObservabilityMiddleware,
    )

    app.add_middleware(HTTPObservabilityMiddleware)
    app.add_middleware(ProfileMiddleware)
    app.add_middleware(RequestTimingMiddleware)

    # Add exception handler for OpenVikingError
    @app.exception_handler(OpenVikingError)
    async def openviking_error_handler(request: Request, exc: OpenVikingError):
        http_status = ERROR_CODE_TO_HTTP_STATUS.get(exc.code, 500)
        capture_public_http_error(code=exc.code, message=exc.message, details=exc.details)
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
        message = _validation_error_message(errors)
        details = {"validation_errors": errors}
        capture_public_http_error(
            code=code,
            message=message,
            details=details,
        )
        return JSONResponse(
            status_code=ERROR_CODE_TO_HTTP_STATUS[code],
            content=Response(
                status="error",
                error=ErrorInfo(
                    code=code,
                    message=message,
                    details=details,
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
        message = _message_from_http_detail(exc.detail)
        capture_public_http_error(code=code, message=message, details=details)
        return JSONResponse(
            status_code=response_status,
            headers=exc.headers,
            content=Response(
                status="error",
                error=ErrorInfo(
                    code=code,
                    message=message,
                    details=details,
                ),
            ).model_dump(exclude_none=True),
        )

    # Catch-all for unhandled exceptions so clients always get JSON
    async def general_error_handler(_request: Request, exc: Exception):
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

    # Keep exception rendering inside the request-ID and CORS layers. This lets
    # those middleware own their response headers without special error paths.
    app.add_middleware(ExceptionMiddleware, handlers={Exception: general_error_handler})
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER],
    )

    app.include_router(system_router)
    app.include_router(metrics_router)
    if not api_enabled:
        return app

    # Configure Bot API if --with-bot is enabled
    if config.with_bot:
        import openviking.server.routers.bot as bot_module

        bot_module.set_bot_api_url(config.bot_api_url)
        bot_module.set_bot_api_key(bot_gateway_token)
        logger.info(f"Bot API proxy enabled, forwarding to {config.bot_api_url}")
    else:
        logger.info("Bot API proxy disabled (use --with-bot to enable)")

    # Register routers
    app.include_router(acl_router)
    app.include_router(admin_router)
    app.include_router(agent_evolution_router)
    app.include_router(compile_router)
    app.include_router(resources_router)
    app.include_router(rules_router)
    app.include_router(filesystem_router)
    app.include_router(mcps_router)
    app.include_router(content_router)
    app.include_router(console_router)
    app.include_router(search_router)
    app.include_router(privacy_configs_router)
    app.include_router(skills_router)
    app.include_router(sessions_router)
    app.include_router(snapshot_router)
    app.include_router(stats_router)
    app.include_router(pack_router)
    app.include_router(debug_router)
    app.include_router(observer_router)
    app.include_router(openviking_assets_router)
    app.include_router(tasks_router)
    app.include_router(user_settings_router)
    app.include_router(watches_router)
    app.include_router(webdav_router)
    app.include_router(bot_router, prefix="/bot/v1")
    app.include_router(bot_studio_router)

    # OAuth 2.1: when enabled, mount the official MCP SDK auth routes
    # (DCR / authorize / token / metadata) plus our authorize page + consent /
    # verify endpoints. SDK routes capture their Provider, whose store is opened
    # and closed by ServerRuntime.
    try:
        ov_cfg = get_openviking_config()
        if ov_cfg.oauth.enabled:
            from mcp.server.auth.routes import create_auth_routes
            from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
            from pydantic import AnyHttpUrl

            from openviking.server.oauth.router import router as oauth_router

            # Custom routes (authorize page + consent / verify endpoints).
            app.include_router(oauth_router)

            # SDK-owned routes (DCR / authorize / token / metadata / revoke).
            # create_auth_routes captures a Provider by reference.
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

            async def _registered_account_role(account_id: str, user_id: str) -> Role | None:
                manager = getattr(app.state, "api_key_manager", None)
                if manager is None:
                    return None
                return await manager.get_registered_user_role(account_id, user_id)

            _route_provider = OpenVikingOAuthProvider(
                store=_route_store,
                issuer=_route_issuer,
                access_token_ttl_seconds=ov_cfg.oauth.access_token_ttl_seconds,
                refresh_token_ttl_seconds=ov_cfg.oauth.refresh_token_ttl_seconds,
                auth_code_ttl_seconds=ov_cfg.oauth.auth_code_ttl_seconds,
                role_resolver=_registered_account_role,
            )
            # Keep route-time instances and request dependencies identical.
            app.state.oauth_store = _route_store
            app.state.oauth_provider = _route_provider

            from openviking.server.oauth.provider import MCP_SCOPE

            sdk_routes = create_auth_routes(
                provider=_route_provider,
                issuer_url=AnyHttpUrl(_route_issuer),
                # default_scopes covers clients whose DCR omits `scope`
                # (ChatGPT does): without it they register scope-less and then
                # fail /authorize with invalid_scope when they request the
                # "mcp" scope advertised in the PRM document. valid_scopes is
                # deliberately NOT set — the SDK would 400 any DCR that carries
                # a scope outside the list, and clients like Claude register
                # with their own scope strings.
                client_registration_options=ClientRegistrationOptions(
                    enabled=True, default_scopes=[MCP_SCOPE]
                ),
                revocation_options=RevocationOptions(enabled=True),
            )
            app.routes.extend(sdk_routes)
            app.state.oauth_config = ov_cfg.oauth
            logger.info(
                "OAuth 2.1 routes mounted",
                extra={"total": len(sdk_routes)},
            )
    except Exception as e:  # noqa: BLE001
        logger.info(
            "OAuth router registration skipped",
            extra={"error_type": type(e).__name__},
        )

    # Favicon routes — always registered so /favicon.* and /mcp/favicon.* never
    # 404, even when web-studio isn't bundled. Source files live in
    # openviking/server/static/ (shipped via package-data, ~30KB total) so they
    # are available in every pip-install / docker / source-tree scenario.
    _server_static_dir = Path(__file__).resolve().parent / "static"
    _favicon_headers = {"Cache-Control": "public, max-age=86400"}
    _favicon_files = {
        "/favicon.ico": ("favicon.ico", "image/x-icon"),
        "/favicon.png": ("favicon-32.png", "image/png"),
        "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
        "/mcp/favicon.ico": ("favicon.ico", "image/x-icon"),
        "/mcp/favicon.png": ("favicon-32.png", "image/png"),
        "/mcp/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
    }

    def _make_favicon_handler(filename: str, media_type: str):
        path = _server_static_dir / filename

        async def _handler():
            return FileResponse(path, media_type=media_type, headers=_favicon_headers)

        return _handler

    for _route, (_fname, _mime) in _favicon_files.items():
        app.add_api_route(_route, _make_favicon_handler(_fname, _mime), include_in_schema=False)

    # Web Studio SPA: serve the static bundle when present so the same OV
    # server origin can host the new frontend at /studio. The directory is
    # populated by the docker `web-studio-builder` stage and shipped inside
    # the openviking python package (see pyproject.toml package-data). Outside
    # docker, set OPENVIKING_WEB_STUDIO_DIR to a local `web-studio/dist` to
    # enable a dev build without rebuilding the wheel.
    _studio_env = os.environ.get("OPENVIKING_WEB_STUDIO_DIR", "").strip()
    if _studio_env:
        _studio_dir = Path(_studio_env)
    else:
        _studio_dir = Path(__file__).resolve().parent.parent / "web_studio" / "dist"

    if _studio_dir.is_dir() and (_studio_dir / "index.html").is_file():
        _studio_root = _studio_dir.resolve()
        _studio_index = _studio_root / "index.html"
        _studio_no_store = {"Cache-Control": "no-store"}

        def _studio_response(path: Path, *, no_store: bool = False) -> FileResponse:
            return FileResponse(path, headers=_studio_no_store if no_store else None)

        @app.get("/", include_in_schema=False)
        async def _root_redirect_to_studio():
            # When web-studio is bundled, treat / as a convenience entry to
            # /studio/ so users hitting the bare origin land on the UI.
            return RedirectResponse(url="/studio/", status_code=302)

        @app.get("/studio", include_in_schema=False)
        async def _studio_root_handler():
            return _studio_response(_studio_index, no_store=True)

        @app.get("/studio/{path:path}", include_in_schema=False)
        async def _studio_assets(path: str):
            # SPA fallback: serve real files when present, otherwise return
            # index.html so TanStack Router can resolve the deep link.
            try:
                requested = (_studio_root / path).resolve()
            except OSError:
                return _studio_response(_studio_index, no_store=True)

            if not requested.is_relative_to(_studio_root):
                return _studio_response(_studio_index, no_store=True)

            if requested.is_file():
                return _studio_response(requested)
            return _studio_response(_studio_index, no_store=True)

        logger.info("Web Studio mounted at /studio from %s", _studio_root)
    else:
        logger.info("Web Studio bundle not found at %s; skipping /studio mount", _studio_dir)

    # MCP endpoint — serves 16 tools (find, search, read, write, edit,
    # list, tree, remember, add_resource, add_skill, list_watches, cancel_watch,
    # grep, glob, forget, health) via streamable HTTP for MCP clients.
    from openviking.server.mcp_endpoint import create_mcp_app

    app.routes.append(
        ScopedRoute("/mcp", endpoint=create_mcp_app(), methods=["GET", "POST", "DELETE"])
    )

    app.state.gateway_app = create_embedded_gateway(
        get_openviking_config(), config, lambda: service
    )
    if app.state.gateway_app is not None:
        # The gateway answers every method and its own WebSocket upgrades below this prefix.
        app.mount(GATEWAY_MOUNT_PATH, app.state.gateway_app)

    return app
