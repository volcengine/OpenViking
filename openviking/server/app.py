# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""FastAPI application for OpenViking HTTP Server."""

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Callable, Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from openviking.server.api_keys import APIKeyManager
from openviking.server.config import ServerConfig, load_server_config, validate_server_config
from openviking.server.dependencies import set_service
from openviking.server.error_mapping import map_exception
from openviking.server.models import ERROR_CODE_TO_HTTP_STATUS, ErrorInfo, Response
from openviking.server.routers import (
    admin_router,
    bot_router,
    content_router,
    debug_router,
    filesystem_router,
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
            logger.info("OpenVikingService initialized")

        assert service is not None
        set_service(service)

        # Initialize APIKeyManager after service (needs VikingFS)
        if config.auth_mode == "api_key" and config.root_api_key:
            api_key_manager = APIKeyManager(
                root_key=config.root_api_key,
                viking_fs=service.viking_fs,
                encryption_enabled=config.encryption_enabled,
            )
            await api_key_manager.load()
            app.state.api_key_manager = api_key_manager
            logger.info(
                "APIKeyManager initialized with encryption_enabled=%s",
                config.encryption_enabled,
            )
        elif config.auth_mode == "trusted":
            app.state.api_key_manager = None
            if config.root_api_key:
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
            app.state.api_key_manager = None
            logger.warning(
                "Dev mode: no root_api_key configured, authentication disabled. "
                "This is allowed because the server is bound to localhost (%s). "
                "Do NOT expose this server to the network without configuring "
                "server.root_api_key in ov.conf.",
                config.host,
            )

        from openviking.metrics.global_api import (
            init_metrics_from_server_config,
        )

        init_metrics_from_server_config(config, app=app, service=service)
        if config.observability.metrics.enabled:
            logger.info("Prometheus metrics enabled at /metrics")

        # Start TaskTracker cleanup loop
        task_tracker = get_task_tracker()
        task_tracker.start_cleanup_loop()

        # Initialize tracer
        from openviking.telemetry import tracer_module

        tracer_module.init_tracer_from_config()

        yield

        # Cleanup
        from openviking.metrics.global_api import shutdown_metrics

        shutdown_metrics(app=app)
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

    # Add request timing middleware
    @app.middleware("http")
    async def add_timing(request: Request, call_next: Callable):
        start_time = time.time()
        response = await call_next(request)
        process_time = time.time() - start_time
        response.headers["X-Process-Time"] = str(process_time)
        return response

    from openviking.metrics.http_middleware import create_http_metrics_middleware

    http_metrics_middleware = create_http_metrics_middleware()

    @app.middleware("http")
    async def add_http_metrics(request: Request, call_next: Callable):
        return await http_metrics_middleware(request, call_next)

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
    app.include_router(bot_router, prefix="/bot/v1")

    return app
