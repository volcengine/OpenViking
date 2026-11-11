# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Static assembly of shared services and server execution roles."""

from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openviking.server.config import ServerConfig
from openviking.service.core import OpenVikingService
from openviking.service.task_tracker import get_task_tracker
from openviking_cli.utils.config import get_openviking_config


def create_auth_plugin(config: ServerConfig):
    from openviking.server.auth.plugins import ApiKeyAuthPlugin, DevAuthPlugin, TrustedAuthPlugin
    from openviking.server.auth.registry import get_registry

    registry = get_registry()
    for plugin in (ApiKeyAuthPlugin, DevAuthPlugin, TrustedAuthPlugin):
        if registry.get(plugin.auth_mode) is None:
            registry.register(plugin)
    mode = config.get_effective_auth_mode()
    plugin_cls = registry.get(mode)
    if plugin_cls is None:
        raise RuntimeError(f"Unknown auth_mode: {mode}")
    return plugin_cls()


def configure_session_runtime(service, config, usage_reporter) -> None:
    sessions = getattr(service, "sessions", None)
    for method, value in (
        ("set_tool_output_externalization_config", config.tool_output_externalization),
        ("set_usage_reporter", usage_reporter),
        ("set_default_user_memory_policy", config.user_config_defaults.memory_policy),
        ("set_default_user_auto_commit_policy", config.user_config_defaults.auto_commit_policy),
    ):
        setter = getattr(sessions, method, None)
        if callable(setter):
            setter(value)
    setter = getattr(service, "set_agent_evolution_config", None)
    if not callable(setter):
        setter = getattr(sessions, "set_agent_evolution_config", None)
    if callable(setter):
        setter(config.agent_evolution)


@dataclass
class SharedResources:
    service: OpenVikingService
    config: ServerConfig
    app: Any = None
    auth_plugin: Any = None
    api_key_manager: Any = None
    ttl_adapter: Any = None
    deletion_service: Any = None
    usage_audit_runtime: Any = None
    oauth_store: Any = None
    gateway_store: Any = None
    gateway_management: Any = None
    cleanup_resources: AsyncExitStack = field(default_factory=AsyncExitStack)

    async def prepare_cleanup(self) -> None:
        if self.app is not None:
            state = self.app.state
            self.oauth_store = getattr(state, "oauth_store", None)
            self.usage_audit_runtime = getattr(state, "usage_audit_runtime", None)
            gateway = getattr(state, "gateway_app", None)
            if gateway is not None:
                self.gateway_store = gateway.state.store
                self.gateway_management = gateway.state.management
            return

        from openviking.observability.usage_audit import (
            init_usage_audit_from_server_config,
            shutdown_usage_audit,
        )

        self.usage_audit_runtime = await init_usage_audit_from_server_config(
            self.config, service=self.service
        )
        self.cleanup_resources.push_async_callback(
            shutdown_usage_audit, runtime=self.usage_audit_runtime
        )
        config = get_openviking_config()
        if config.oauth.enabled:
            from openviking.server.oauth.storage import OAuthStore

            workspace = Path(config.storage.workspace).expanduser().resolve()
            self.oauth_store = OAuthStore(workspace / config.oauth.db_filename)
            self.cleanup_resources.push_async_callback(self.oauth_store.close)
            await self.oauth_store.initialize()
        if config.gateway.enabled:
            from openviking.crypto.config import bootstrap_encryption
            from openviking.server.gateway_host import embedded_gateway_config
            from openviking_gateway.app import open_stores

            encryptor = await bootstrap_encryption({"encryption": config.encryption.model_dump()})
            self.gateway_store, self.gateway_management, close = open_stores(
                embedded_gateway_config(config, self.config), encryptor
            )
            self.cleanup_resources.push_async_callback(close)
            await self.gateway_store.initialize()
            await self.gateway_management.initialize()

    async def prepare(self) -> None:
        await self.prepare_cleanup()
        await self.service.initialize(start_background=False)
        await self.service.apply_agent_evolution_config()
        if self.app is not None:
            from openviking.server.app import _initialize_auth_plugin, _initialize_ttl_adapter

            try:
                await _initialize_auth_plugin(self.app, self.service, self.config)
            finally:
                self.auth_plugin = getattr(self.app.state, "auth_plugin", None)
            self.api_key_manager = self.app.state.api_key_manager
            await _initialize_ttl_adapter(self.app, self.service, self.config)
            self.ttl_adapter = getattr(self.app.state, "ttl_adapter", None)
        else:
            from openviking.ttl.wiring import build_ttl_adapter, install_ttl_hook, set_ttl_adapter

            self.auth_plugin = create_auth_plugin(self.config)
            self.api_key_manager = await self.auth_plugin.initialize_runtime(
                self.service, self.config, service_name=get_openviking_config().service_name
            )
            self.ttl_adapter = build_ttl_adapter(self.config.ttl_adapter)
            set_ttl_adapter(self.ttl_adapter)
            if self.ttl_adapter is not None:
                self.ttl_adapter.bind_runtime(self.service, self.api_key_manager)
                self.service.configure_ttl(self.ttl_adapter)
                install_ttl_hook(self.service.viking_fs, self.ttl_adapter)

        manager = self.api_key_manager
        if manager is not None:
            self.service.compile.lifecycle.resolve_groups = manager.get_user_group_ids
            if self.service.compile_scheduler is not None:
                self.service.compile_scheduler.set_identity_resolver(
                    manager.resolve_identity, manager.get_user_group_ids
                )
        # Packaged files belong to internal startup identities. Persist them
        # before installing the registered-user task guard; processing is durable
        # and must not wait for consumers that have not started yet.
        await self.service.reconcile_default_system_assets()
        if manager is not None and get_openviking_config().reconcile_system_assets_on_startup:
            await self.service.reconcile_system_assets(manager)
        from openviking.service.deletion import setup_deletion

        state = self.app.state if self.app is not None else None
        self.deletion_service = await setup_deletion(
            service=self.service,
            manager=manager,
            oauth_store=self.oauth_store,
            usage_audit_runtime=self.usage_audit_runtime,
            gateway_store=self.gateway_store,
            gateway_management=self.gateway_management,
            require_registered_identity=self.config.get_effective_auth_mode() == "api_key",
        )
        if state is not None:
            state.deletion_service = self.deletion_service


@dataclass
class QueueWorkerRuntime:
    shared: SharedResources

    async def start(self) -> None:
        await self.shared.service.start_queue_worker()

    async def stop(self) -> None:
        await self.shared.service.stop_queue_worker()


@dataclass
class SchedulerRuntime:
    shared: SharedResources

    async def start(self) -> None:
        await self.shared.service.start_scheduler()

    async def stop(self) -> None:
        await self.shared.service.stop_scheduler()


class ServerRuntime:
    """Own one shared service graph and the configured execution roles."""

    def __init__(self, service, config, *, app=None, owns_service=True):
        self.shared = SharedResources(service, config, app)
        self.scheduler = SchedulerRuntime(self.shared) if "scheduler" in config.roles else None
        self.queue_worker = (
            QueueWorkerRuntime(self.shared) if "queue_worker" in config.roles else None
        )
        self.owns_service = owns_service
        self.started = False
        self.stopped = False

    @staticmethod
    def _task_alive(task) -> bool:
        return task is not None and not task.done()

    def is_healthy(self) -> bool:
        """Return whether configured roles and their owned loops are still running."""
        if not self.started or self.stopped:
            return False
        tracker = get_task_tracker()
        if not all(
            self._task_alive(task)
            for task in (tracker._cleanup_task, tracker._cancel_poll_task)
        ):
            return False
        service = self.shared.service
        if self.queue_worker is not None:
            manager = getattr(service, "_queue_manager", None)
            if manager is None or not manager.is_running():
                return False
            if any(not thread.is_alive() for thread in manager._queue_threads.values()):
                return False
        if self.scheduler is not None:
            watch = getattr(service, "_watch_runtime", None)
            if watch is not None and (
                not watch.is_running
                or watch._leader_enabled
                and not self._task_alive(watch._loop_task)
            ):
                return False
            for name in (
                "_session_auto_commit_scheduler",
                "_rule_cleanup_scheduler",
                "_compile_scheduler",
            ):
                component = getattr(service, name, None)
                if component is not None and (
                    not getattr(component, "_running", True)
                    or not self._task_alive(component._task)
                ):
                    return False
            expiry = getattr(service, "_expiry_runtime", None)
            if expiry is not None and (
                not expiry._storage_started
                or not expiry._state.accepting_calls
                or expiry._io._thread is None
                or not expiry._io._thread.is_alive()
                or any(task.done() for task in expiry._tasks.values())
            ):
                return False
        return True

    async def start(self) -> None:
        if self.started:
            return
        if self.stopped:
            raise RuntimeError("A stopped ServerRuntime cannot be restarted")
        try:
            await self.shared.prepare()
            await self.shared.service.prepare_background(
                queue_worker=self.queue_worker is not None, scheduler=self.scheduler is not None
            )
            get_task_tracker().start_cleanup_loop()
            if self.scheduler is not None:
                await self.scheduler.start()
            if self.queue_worker is not None:
                await self.queue_worker.start()
            self.shared.service._initialized = True
            self.started = True
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        if self.stopped:
            return
        self.started = False
        # Core drains producers, expiry callbacks and queue writers before stores.
        # Account state must stay available throughout that drain, including rollback.
        if self.owns_service:
            await self.shared.service.close()
        else:
            await self.shared.service.stop_background()
        await self.shared.cleanup_resources.aclose()
        if self.shared.auth_plugin is not None:
            await self.shared.auth_plugin.shutdown()
        self.stopped = True
        self.started = False
