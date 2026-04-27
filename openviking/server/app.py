# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""FastAPI application for OpenViking HTTP Server."""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Callable, Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from openviking.server.api_keys import APIKeyManager
from openviking.server.config import (
    ServerConfig,
    load_bot_gateway_token,
    load_server_config,
    validate_server_config,
)
from openviking.server.dependencies import set_service
from openviking.server.error_mapping import map_exception
from openviking.server.identity import AuthMode
from openviking.server.models import ERROR_CODE_TO_HTTP_STATUS, ErrorInfo, Response
from openviking.server.routers import (
    admin_router,
    bot_router,
    content_router,
    debug_router,
    filesystem_router,
    maintenance_router,
    metrics_router,
    observer_router,
    pack_router,
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
from openviking_cli.utils.logger import init_otel_log_handler_from_server_config

logger = get_logger(__name__)


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

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Application lifespan handler."""
        nonlocal service
        owns_service = service is None
        if owns_service:
            service = OpenVikingService()
            await service.initialize()

        assert service is not None
        set_service(service)

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

        from openviking.metrics.global_api import (
            init_metrics_from_server_config,
        )

        init_metrics_from_server_config(config, app=app, service=service)
        if config.observability.metrics.enabled:
            logger.info("Prometheus metrics enabled at /metrics")

        # Start TaskTracker cleanup loop
        task_tracker = get_task_tracker()
        task_tracker.start_cleanup_loop()

        # Initialize tracing and OTLP log export from server.observability.
        from openviking.telemetry import tracer_module

        tracer_module.init_tracer_from_server_config(config)
        init_otel_log_handler_from_server_config(config)

        yield

        # Cleanup
        from openviking.metrics.global_api import shutdown_metrics_async

        await shutdown_metrics_async(app=app)
        task_tracker.stop_cleanup_loop()
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

    # Add HTTP observability middleware first (metrics, tracing)
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
    app.include_router(search_router)
    app.include_router(relations_router)
    app.include_router(sessions_router)
    app.include_router(stats_router)
    app.include_router(pack_router)
    app.include_router(debug_router)
    app.include_router(observer_router)
    app.include_router(metrics_router)
    app.include_router(tasks_router)
    app.include_router(webdav_router)
    app.include_router(maintenance_router)
    app.include_router(bot_router, prefix="/bot/v1")

    return app
