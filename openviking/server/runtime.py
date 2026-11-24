# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Assembly and lifecycle ownership for one hosted OpenViking execution environment."""

import asyncio
from contextlib import AsyncExitStack

from fastapi import FastAPI

from openviking.server.config import ServerConfig
from openviking.server.dependencies import set_service
from openviking.service.core import OpenVikingService
from openviking_cli.utils import get_logger
from openviking_cli.utils.logger import init_otel_log_handler_from_server_config

logger = get_logger(__name__)


async def initialize_auth_plugin(
    app: FastAPI, service: OpenVikingService, config: ServerConfig
) -> None:
    """Keep request authentication and its private resources in the auth plugin."""
    from openviking.config.binding import validate_cluster_resources
    from openviking.server.auth.plugins import (
        ApiKeyAuthPlugin,
        DevAuthPlugin,
        TrustedAuthPlugin,
    )
    from openviking.server.auth.registry import get_registry
    from openviking_cli.utils.config import get_openviking_config

    registry = get_registry()
    for plugin_cls in (DevAuthPlugin, ApiKeyAuthPlugin, TrustedAuthPlugin):
        if registry.get(plugin_cls.auth_mode) is None:
            registry.register(plugin_cls)
    mode = config.get_effective_auth_mode()
    plugin_cls = registry.get(mode)
    if plugin_cls is None:
        raise RuntimeError(f"Unknown auth_mode: {mode}")
    if mode in {"dev", "trusted"}:
        try:
            validate_cluster_resources(get_openviking_config())
        except ValueError as error:
            raise ValueError(
                f"server.auth_mode='{mode}' requires valid Cluster VLM, Embedding, "
                f"and VectorDB configuration: {error}"
            ) from error
    app.state.auth_plugin = plugin_cls()
    await app.state.auth_plugin.initialize(app, service, config)
    logger.info("Auth plugin initialized: %s", mode)


class ServerRuntime:
    """Own one app lifespan, optionally borrowing caller-owned core storage."""

    def __init__(
        self, app: FastAPI, service: OpenVikingService, config: ServerConfig, *, owns_service: bool
    ) -> None:
        self.app = app
        self.service = service
        self.config = config.model_copy(deep=True)
        self.owns_service = owns_service
        self._stack = AsyncExitStack()
        self._mcp_stack = AsyncExitStack()

    def _mount_ttl_routes(self) -> None:
        if "api" in self.config.roles and self.service.ttl_adapter is not None:
            from openviking.ttl.internal import router

            self.app.include_router(router)

    async def wire(self) -> None:
        """Wire host account capabilities and whole deletion before recovery."""
        await initialize_auth_plugin(self.app, self.service, self.config)
        self.service.wire_runtime(self.app.state.api_key_manager)
        self.app.state.ttl_adapter = self.service.ttl_adapter
        self._mount_ttl_routes()
        await self.service.apply_agent_evolution_config()
        from openviking.service.deletion import setup_deletion

        self.app.state.deletion_service = await setup_deletion(
            service=self.service,
            manager=self.app.state.api_key_manager,
            oauth_store=getattr(self.app.state, "oauth_store", None),
            usage_audit_runtime=getattr(self.app.state, "usage_audit_runtime", None),
            gateway_app=self.app.state.gateway_app,
            require_registered_identity=self.config.get_effective_auth_mode() == "api_key",
        )

    async def start(self, bot_gateway_token: str) -> None:
        app, service, config, stack = self.app, self.service, self.config, self._stack
        service.configure_runtime(config)
        set_service(service)
        await service.initialize()
        if config.with_bot:
            service.compile.configure_local_backend(config.bot_api_url, bot_gateway_token)

        from openviking.metrics.global_api import (
            init_metrics_from_server_config,
            shutdown_metrics_async,
        )
        from openviking.observability.usage_audit import (
            init_usage_audit_from_server_config,
            shutdown_usage_audit,
        )

        stack.push_async_callback(shutdown_metrics_async, app=app)
        if config.observability.metrics.enabled:
            from openviking.metrics.core.runtime import (
                install_executor_monitor,
                uninstall_executor_monitor,
            )

            install_executor_monitor()
            stack.callback(uninstall_executor_monitor)
        init_metrics_from_server_config(config, app=app, service=service)
        stack.push_async_callback(shutdown_usage_audit, app=app)
        await init_usage_audit_from_server_config(config, app=app, service=service)
        from openviking.observability.reporting import start_reporting, stop_reporting

        stack.push_async_callback(stop_reporting)
        app.state.event_reporter = await start_reporting(config.observability.reporting)
        oauth_store = getattr(app.state, "oauth_store", None)
        if oauth_store is not None:
            stack.push_async_callback(oauth_store.close)
            await oauth_store.initialize()

            async def oauth_gc_loop() -> None:
                while True:
                    await asyncio.sleep(60)
                    try:
                        await oauth_store.gc_expired()
                    except Exception as error:
                        logger.info(
                            "OAuth expiration cleanup failed; will retry",
                            extra={"error_type": type(error).__name__},
                        )

            task = asyncio.create_task(oauth_gc_loop())

            async def stop_oauth_gc() -> None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

            stack.push_async_callback(stop_oauth_gc)

        from openviking.telemetry import tracer_module

        tracer_module.init_tracer_from_server_config(config)
        init_otel_log_handler_from_server_config(config)
        if "api" in config.roles:
            from openviking.server.mcp_endpoint import mcp_lifespan

            await self._mcp_stack.enter_async_context(mcp_lifespan())
            stack.push_async_callback(self._mcp_stack.aclose)
        gateway = app.state.gateway_app
        if gateway is not None:
            await stack.enter_async_context(gateway.router.lifespan_context(gateway))
        await self.wire()
        await service.recover()
        manager = app.state.api_key_manager
        if manager is not None and service._config.reconcile_system_assets_on_startup:
            count = await service.reconcile_system_assets(manager)
            logger.info("Reconciled system assets for %d active accounts", count)
        if (
            gateway is not None
            and config.get_effective_auth_mode() == "dev"
            and service._config.enable_default_resources
        ):
            from openviking_gateway.app import seed_defaults

            await seed_defaults(gateway, "default")
        await service.start_background(
            queue_worker="queue_worker" in config.roles,
            scheduler="scheduler" in config.roles,
        )

    async def close(self) -> None:
        """Drain execution, close plugins and host stores, then release core resources."""
        # Do not close dependencies while their execution is still running.
        try:
            await self.service.stop_background()
            self.service.detach_runtime()
        except BaseException:
            # MCP must exit in this task; retain dependencies of unsettled writers.
            await self._mcp_stack.aclose()
            raise
        try:
            plugin = getattr(self.app.state, "auth_plugin", None)
            if plugin is not None:
                await plugin.shutdown()
        finally:
            try:
                await self._stack.aclose()
            finally:
                await self.service.release_runtime()
                if self.owns_service:
                    await self.service.release_storage()
