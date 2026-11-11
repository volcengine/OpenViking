# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Role composition, producer reporting and dependency-safe shutdown."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from openviking.server.config import ServerConfig
from openviking.server.runtime import ServerRuntime
from openviking.service.core import OpenVikingService
from openviking_cli.utils.config.memory_config import SessionAutoCommitConfig

ROLE_COMBINATIONS = [
    ["api"],
    ["queue_worker"],
    ["scheduler"],
    ["api", "queue_worker"],
    ["api", "scheduler"],
    ["queue_worker", "scheduler"],
    ["api", "queue_worker", "scheduler"],
]


def test_roles_default_and_validation():
    assert ServerConfig().roles == ["api", "queue_worker", "scheduler"]
    assert ServerConfig(roles=["scheduler", "scheduler"]).roles == ["scheduler"]
    for roles in ([], ["worker"], ["api", "typo"]):
        with pytest.raises(ValidationError):
            ServerConfig(roles=roles)


@pytest.mark.parametrize("roles", ROLE_COMBINATIONS)
@pytest.mark.parametrize("separate_publisher", [True, False])
async def test_core_starts_selected_roles_and_keeps_all_reporting(
    monkeypatch,
    roles,
    separate_publisher,
):
    events = []
    expiry_instances = []

    class ExpiryRuntime:
        def __init__(self, config, *, roles):
            self.roles = roles
            self.scheduler = Mock()
            self.start_storage = AsyncMock(side_effect=lambda: events.append("expiry-storage"))
            self.start_roles = AsyncMock(side_effect=lambda: events.append("expiry-roles"))
            expiry_instances.append(self)

    def scheduled(name):
        return SimpleNamespace(
            start=AsyncMock(side_effect=lambda: events.append(name)),
            stop=AsyncMock(),
            install_expiry=Mock(),
        )

    config = SimpleNamespace(
        expiry=SimpleNamespace(
            publisher_separate_process=separate_publisher,
            session_consumer_concurrency=2,
            validate_for_runtime=Mock(),
        ),
        memory=SimpleNamespace(
            session_auto_commit=SessionAutoCommitConfig(enabled=True, idle_backend="redis"),
        ),
        enable_watch_scheduler=True,
        reconcile_system_assets_on_startup=False,
        pdf=SimpleNamespace(strategy="auto", mineru_endpoint=None),
    )
    monkeypatch.setattr("openviking.service.core.get_openviking_config", lambda: config)
    monkeypatch.setattr("openviking.scheduling.expiry.runtime.ExpiryRuntime", ExpiryRuntime)
    service = OpenVikingService.__new__(OpenVikingService)
    service._initialized = True
    service._shared_initialized = True
    service._background_prepared = False
    service._config = config
    service._session_service = SimpleNamespace(configure_expiry=Mock())
    service._session_auto_commit_scheduler = scheduled("session-scan")
    service._watch_runtime = scheduled("watch-scan")
    service._rule_cleanup_scheduler = scheduled("rule-scan")
    service._compile_scheduler = scheduled("compile-scan")
    service._ttl_adapter = SimpleNamespace(
        supports_config=lambda: True,
        install_expiry=Mock(),
        start=AsyncMock(side_effect=lambda: events.append("ttl-maintenance")),
        start_reporting=AsyncMock(side_effect=lambda: events.append("ttl-reporting")),
    )
    service._queue_manager = SimpleNamespace(start=Mock(side_effect=lambda: events.append("queue")))
    service._runtime_config_manager = SimpleNamespace(start_refresh_loop=Mock())
    service.reconcile_default_system_assets = AsyncMock()
    await service.start_background(
        queue_worker="queue_worker" in roles,
        scheduler="scheduler" in roles,
    )
    expiry = expiry_instances[0]
    assert expiry.roles.report is True
    assert expiry.roles.consume == ("queue_worker" in roles)
    assert expiry.roles.publish == ("scheduler" in roles and not separate_publisher)
    service._session_service.configure_expiry.assert_called_once()
    service._compile_scheduler.install_expiry.assert_called_once_with(expiry.scheduler)
    service._ttl_adapter.install_expiry.assert_called_once_with(expiry.scheduler)
    service._runtime_config_manager.start_refresh_loop.assert_called_once()
    scans = {"session-scan", "watch-scan", "rule-scan", "compile-scan"}
    assert (scans <= set(events)) == ("scheduler" in roles)
    assert ("queue" in events) == ("queue_worker" in roles)
    if "queue_worker" in roles:
        assert (
            events.index("ttl-maintenance") < events.index("expiry-roles") < events.index("queue")
        )
    else:
        assert "ttl-reporting" in events
        assert "ttl-maintenance" not in events


@pytest.mark.parametrize("fail_stage", ["shared", "expiry", "scheduler", "queue"])
async def test_runtime_rolls_back_startup_with_accounts_open_until_service_closes(
    monkeypatch, fail_stage
):
    events = []
    plugin = SimpleNamespace(shutdown=AsyncMock(side_effect=lambda: events.append("auth-close")))

    async def prepare(shared):
        shared.auth_plugin = plugin
        events.append("shared")
        if fail_stage == "shared":
            raise RuntimeError("startup failed")

    def step(name):
        async def run(**kwargs):
            events.append(name)
            if fail_stage == name:
                raise RuntimeError("startup failed")

        return run

    monkeypatch.setattr("openviking.server.runtime.SharedResources.prepare", prepare)
    monkeypatch.setattr(
        "openviking.server.runtime.get_task_tracker",
        lambda: SimpleNamespace(start_cleanup_loop=Mock()),
    )
    service = SimpleNamespace(
        prepare_background=step("expiry"),
        start_scheduler=step("scheduler"),
        start_queue_worker=step("queue"),
        reconcile_default_system_assets=AsyncMock(),
        close=AsyncMock(side_effect=lambda: events.append("service-close")),
    )
    runtime = ServerRuntime(service, ServerConfig())
    assert runtime.scheduler.shared is runtime.queue_worker.shared is runtime.shared
    with pytest.raises(RuntimeError, match="startup failed"):
        await runtime.start()
    assert events[-2:] == ["service-close", "auth-close"]
    assert not runtime.started
    await runtime.stop()
    service.close.assert_awaited_once()
    plugin.shutdown.assert_awaited_once()


async def test_failed_drain_keeps_account_store_open():
    service = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("still running")))
    runtime = ServerRuntime(service, ServerConfig(roles=["queue_worker"]))
    runtime.shared.auth_plugin = SimpleNamespace(shutdown=AsyncMock())
    cleanup = AsyncMock()
    runtime.shared.cleanup_resources.push_async_callback(cleanup)
    with pytest.raises(RuntimeError, match="still running"):
        await runtime.stop()
    runtime.shared.auth_plugin.shutdown.assert_not_awaited()
    cleanup.assert_not_awaited()
    assert runtime.stopped is False


async def test_ttl_reporting_retries_without_maintenance(monkeypatch):
    from openviking.ttl.runtime import DirectoryTTL

    adapter = DirectoryTTL(resource_id="roles-test", writer=True)
    flushed = asyncio.Event()

    async def flush():
        flushed.set()

    monkeypatch.setattr(adapter, "_flush_registrations", flush)
    monkeypatch.setattr(adapter, "_run", AsyncMock())
    await adapter.start_reporting()
    adapter._recycle.add("account")
    adapter._wake_worker()
    await adapter._enqueue_registration(("account", "resources", "viking://resources/date"), 1)
    await asyncio.wait_for(flushed.wait(), 1)
    assert adapter._task is None
    adapter._run.assert_not_awaited()
    adapter._retry.clear()
    await adapter.aclose()


async def test_sdk_default_still_starts_all_background_roles(tmp_path, monkeypatch):
    from openviking.service.task_tracker import set_task_tracker
    from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton
    from tests.utils.mock_agfs import MockLocalAGFS

    OpenVikingConfigSingleton.reset_instance()
    OpenVikingConfigSingleton.initialize(
        config_dict={
            "storage": {"workspace": str(tmp_path)},
            "embedding": {
                "dense": {"provider": "openai", "model": "test", "api_key": "test", "dimension": 8}
            },
            "reconcile_system_assets_on_startup": False,
        }
    )
    agfs = MockLocalAGFS(root_path=tmp_path / "agfs")
    monkeypatch.setattr("openviking.utils.agfs_utils.create_agfs_client", lambda *a, **kw: agfs)
    service = OpenVikingService()
    try:
        await asyncio.wait_for(service.initialize(), 10)
        assert service._initialized
        assert service.watch_runtime.is_running
        assert service._queue_manager.is_running()
    finally:
        await service.close()
        set_task_tracker(None)
        OpenVikingConfigSingleton.reset_instance()


async def test_borrowed_service_drains_without_closing_shared_storage():
    service = SimpleNamespace(stop_background=AsyncMock(), close=AsyncMock())
    runtime = ServerRuntime(service, ServerConfig(), owns_service=False)
    runtime.shared.auth_plugin = SimpleNamespace(shutdown=AsyncMock())
    await runtime.stop()
    service.stop_background.assert_awaited_once()
    service.close.assert_not_awaited()
    runtime.shared.auth_plugin.shutdown.assert_awaited_once()


@pytest.mark.parametrize("roles", ROLE_COMBINATIONS)
async def test_headless_and_api_share_guards_before_any_consumer_starts(
    tmp_path, monkeypatch, roles
):
    from openviking.service.task_tracker import set_task_tracker
    from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton
    from tests.utils.mock_agfs import MockLocalAGFS

    OpenVikingConfigSingleton.reset_instance()
    OpenVikingConfigSingleton.initialize(
        config_dict={
            "storage": {"workspace": str(tmp_path)},
            "embedding": {
                "dense": {"provider": "openai", "model": "test", "api_key": "test", "dimension": 8}
            },
            "reconcile_system_assets_on_startup": True,
        }
    )
    agfs = MockLocalAGFS(root_path=tmp_path / "agfs")
    monkeypatch.setattr(
        agfs,
        "ensure_parent_dirs",
        lambda path, **kw: agfs._resolve(path).parent.mkdir(parents=True, exist_ok=True),
        raising=False,
    )
    monkeypatch.setattr("openviking.utils.agfs_utils.create_agfs_client", lambda *a, **kw: agfs)
    service = OpenVikingService()
    config = ServerConfig(roles=roles, root_api_key="runtime-test-key")
    app = None
    if "api" in roles:
        from openviking.server.app import create_app

        app = create_app(config, service=service)
    runtime = ServerRuntime(service, config, app=app)
    # Inspect the real graph at the actual queue consumption boundary.
    original_start = service._queue_manager.start

    def start():
        assert runtime.shared.api_key_manager is not None
        assert runtime.shared.deletion_service is not None
        assert service.compile is service._compile_service
        assert not runtime.started
        original_start()

    monkeypatch.setattr(service._queue_manager, "start", start)
    try:
        await asyncio.wait_for(runtime.start(), 10)
        assert runtime.started
        assert service._initialized
        assert service.watch_manager is not None
        assert service.watch_runtime.is_running == ("scheduler" in roles)
        assert service._queue_manager.is_running() == ("queue_worker" in roles)
        assert runtime.shared.auth_plugin._api_key_manager is runtime.shared.api_key_manager
        queue = service._queue_manager
        await asyncio.wait_for(runtime.stop(), 10)
        assert runtime.stopped
        assert service._agfs_client is None
        assert runtime.shared.auth_plugin._api_key_manager is None
        assert queue._agfs is None
        assert queue._queues == {}
    finally:
        await runtime.stop()
        set_task_tracker(None)
        OpenVikingConfigSingleton.reset_instance()


async def test_headless_signal_drains_without_constructing_http_app(monkeypatch):
    import signal

    from openviking.server import headless

    events = []
    handlers = {}
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        loop, "add_signal_handler", lambda sig, handler: handlers.update({sig: handler})
    )
    monkeypatch.setattr(loop, "remove_signal_handler", lambda sig: None)
    monkeypatch.setattr(headless.signal, "signal", lambda *args: None)
    monkeypatch.setattr(headless, "OpenVikingService", lambda: SimpleNamespace())
    monkeypatch.setattr(
        "openviking.server.app.create_app", lambda *args, **kwargs: pytest.fail("HTTP app created")
    )
    monkeypatch.setattr("openviking.server.dependencies.set_service", lambda service: None)
    monkeypatch.setattr("openviking.server.dependencies.set_server_config", lambda config: None)
    monkeypatch.setattr(
        "openviking.metrics.global_api.init_metrics_from_server_config",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr("openviking.metrics.global_api.shutdown_metrics_async", AsyncMock())
    monkeypatch.setattr(
        "openviking.observability.usage_audit.init_usage_audit_from_server_config", AsyncMock()
    )
    monkeypatch.setattr("openviking.observability.usage_audit.shutdown_usage_audit", AsyncMock())
    monkeypatch.setattr(
        "openviking.telemetry.tracer_module.init_tracer_from_server_config", lambda config: None
    )
    monkeypatch.setattr(headless, "init_otel_log_handler_from_server_config", lambda config: None)
    monkeypatch.setattr(
        "openviking.usage_reporter.config.build_usage_reporter", lambda config: None
    )

    class Runtime:
        def __init__(self, service, config):
            self.shared = SimpleNamespace()

        async def start(self):
            events.append("start")
            handlers[signal.SIGTERM]()

        async def stop(self):
            events.append("stop")

    monkeypatch.setattr(headless, "ServerRuntime", Runtime)
    await asyncio.wait_for(headless.serve(ServerConfig(roles=["queue_worker"])), 1)
    assert events == ["start", "stop"]


async def test_api_lifespan_closes_owned_resources_after_startup_failure(monkeypatch):
    from contextlib import asynccontextmanager

    from openviking.server import app as app_module

    service = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(app_module, "OpenVikingService", lambda: service)
    monkeypatch.setattr(
        app_module,
        "_initialize_runtime_state",
        AsyncMock(side_effect=RuntimeError("startup failed")),
    )
    monkeypatch.setattr(
        "openviking.metrics.global_api.init_metrics_from_server_config", lambda *a, **kw: None
    )
    metrics_close = AsyncMock()
    audit_close = AsyncMock()
    monkeypatch.setattr("openviking.metrics.global_api.shutdown_metrics_async", metrics_close)
    monkeypatch.setattr("openviking.observability.usage_audit.shutdown_usage_audit", audit_close)
    monkeypatch.setattr(
        "openviking.observability.usage_audit.init_usage_audit_from_server_config", AsyncMock()
    )
    monkeypatch.setattr(
        "openviking.telemetry.tracer_module.init_tracer_from_server_config", lambda config: None
    )
    monkeypatch.setattr(app_module, "init_otel_log_handler_from_server_config", lambda config: None)

    @asynccontextmanager
    async def mcp_lifespan():
        yield

    monkeypatch.setattr("openviking.server.mcp_endpoint.mcp_lifespan", mcp_lifespan)
    app = app_module.create_app(ServerConfig(roles=["api"]))
    with pytest.raises(RuntimeError, match="startup failed"):
        async with app.router.lifespan_context(app):
            pytest.fail("API accepted traffic before startup completed")
    service.close.assert_awaited_once()
    metrics_close.assert_awaited_once()
    audit_close.assert_awaited_once()


@pytest.mark.parametrize("user_id", [None, "bob"], ids=["account", "user"])
@pytest.mark.parametrize("explicit_storage", [False, True], ids=["default", "encrypted-custom"])
async def test_headless_deletion_cleans_api_stores(
    tmp_path, monkeypatch, user_id, explicit_storage
):
    from contextlib import AsyncExitStack

    from openviking.models.embedder.base import DenseEmbedderBase, EmbedResult
    from openviking.observability.events import try_publish_event
    from openviking.observability.usage_audit import (
        init_usage_audit_from_server_config,
        shutdown_usage_audit,
    )
    from openviking.server.identity import RequestContext, Role
    from openviking.server.oauth.storage import OAuthStore
    from openviking.service.task_store import SYSTEM_TASK_ACCOUNT_ID, SYSTEM_TASK_USER_ID
    from openviking.service.task_tracker import TaskStatus, get_task_tracker, set_task_tracker
    from openviking_cli.session.user_id import UserIdentifier
    from openviking_cli.utils.config import get_openviking_config
    from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton
    from openviking_gateway.app import open_stores
    from openviking_gateway.storage import digest

    OpenVikingConfigSingleton.reset_instance()
    OpenVikingConfigSingleton.initialize(
        config_dict={
            "storage": {"workspace": str(tmp_path)},
            "embedding": {
                "dense": {"provider": "openai", "model": "test", "api_key": "test", "dimension": 8}
            },
            "reconcile_system_assets_on_startup": False,
            "oauth": {"enabled": True, "db_filename": "custom-oauth.sqlite3"},
            "gateway": {
                "enabled": True,
                **({"storage_path": str(tmp_path / "custom-gateway")} if explicit_storage else {}),
            },
            "encryption": {
                "enabled": explicit_storage,
                "local": {"key_file": str(tmp_path / "master.key")},
            },
        }
    )
    config = ServerConfig(roles=["queue_worker"], root_api_key="cleanup-test")
    config.observability.usage_audit.flush_interval_seconds = 0.01
    if explicit_storage:
        config.observability.usage_audit.sqlite_path = str(tmp_path / "custom-audit.sqlite3")

    class FakeEmbedder(DenseEmbedderBase):
        def __init__(self):
            super().__init__(model_name="test")

        def embed(self, text, is_query=False):
            return EmbedResult(dense_vector=[0.1] * 8)

        def get_dimension(self):
            return 8

    monkeypatch.setattr(
        "openviking_cli.utils.config.embedding_config.EmbeddingConfig.get_embedder",
        lambda self: FakeEmbedder(),
    )
    monkeypatch.setattr(
        "openviking_gateway.app.create_app", lambda *a, **kw: pytest.fail("HTTP gateway created")
    )
    service = OpenVikingService()
    runtime = ServerRuntime(service, config)
    try:
        async with AsyncExitStack() as stack:
            # Independent API-side handles: worker cleanup must reach their persisted data.
            ov_config = get_openviking_config()
            from openviking.crypto.config import bootstrap_encryption

            encryptor = await bootstrap_encryption(
                {"encryption": ov_config.encryption.model_dump()}
            )
            gateway_config = ov_config.gateway.model_copy(
                update={
                    "storage_path": str(
                        tmp_path / ("custom-gateway" if explicit_storage else "gateway")
                    )
                }
            )
            store, management, close = open_stores(gateway_config, encryptor)
            stack.push_async_callback(close)
            await store.initialize()
            await management.initialize()
            oauth = OAuthStore(tmp_path / ov_config.oauth.db_filename)
            stack.push_async_callback(oauth.close)
            await oauth.initialize()
            audit = await init_usage_audit_from_server_config(config)
            stack.push_async_callback(audit.store.close)
            for account, user in (("target", "alice"), ("target", "bob"), ("other", "bob")):
                token = f"{account}-{user}"
                await management.save(account, "users", user, {"user_id": user})
                await management.log(account, {"request": token}, user_id=user)
                await store.keep(digest(account + "\0" + user + "\0chat"), ["session"], [])
                await oauth.insert_access(
                    token_plain=token,
                    client_id="client",
                    account_id=account,
                    user_id=user,
                    role="user",
                    scope="mcp",
                    resource=None,
                    authorizing_key_fp="a" * 64,
                    ttl_seconds=3600,
                )
                try_publish_event(
                    "http.request",
                    {
                        "request_id": token,
                        "account_id": account,
                        "user_id": user,
                        "method": "POST",
                        "route": "/api/v1/search/find",
                        "status": "200",
                        "duration_seconds": 0.01,
                    },
                )
            await shutdown_usage_audit(runtime=audit)
            await runtime.start()
            stack.push_async_callback(runtime.stop)
            manager = runtime.shared.api_key_manager
            await manager.create_account("target", "alice")
            await manager.register_user("target", "bob")
            result = await runtime.shared.deletion_service.delete(
                "target",
                user_id,
                actor=RequestContext(user=UserIdentifier("root", "root"), role=Role.ROOT),
            )
            task = await get_task_tracker().wait(
                result["task_id"],
                account_id=SYSTEM_TASK_ACCOUNT_ID,
                user_id=SYSTEM_TASK_USER_ID,
                timeout=10,
            )
            assert task.status == TaskStatus.COMPLETED, task.error
            assert task.result == {"deleted": True}
            # Reopen the projection to observe deletion through an independent connection.
            await audit.store.initialize()
            for account, user in (("target", "alice"), ("target", "bob"), ("other", "bob")):
                removed = account == "target" and (user_id is None or user == user_id)
                assert (await oauth.load_access(f"{account}-{user}") is None) == removed
                assert (await management.get(account, "users", user) is None) == removed
                assert (await management.logs(account, user_id=user) == []) == removed
                logs = await audit.store.query_audit_logs(account_id=account, user_id=user)
                assert logs["total"] == (0 if removed else 1)
                scope = digest(account + "\0" + user + "\0chat")
                if removed:
                    with pytest.raises(RuntimeError, match="data has been deleted"):
                        await store.keep(scope, ["session"], [])
                else:
                    await store.keep(scope, ["session"], [])
            await audit.store.close()
    finally:
        await runtime.stop()
        set_task_tracker(None)
        OpenVikingConfigSingleton.reset_instance()


@pytest.mark.parametrize("fail_stage", ["disabled", "oauth", "kernel", "management", "core", "api"])
async def test_headless_cleanup_resources_close_after_drain(monkeypatch, tmp_path, fail_stage):
    events = []
    enabled = fail_stage != "disabled"
    config = ServerConfig(roles=["queue_worker"])
    config.observability.usage_audit.enabled = enabled
    ov_config = SimpleNamespace(
        storage=SimpleNamespace(workspace=str(tmp_path)),
        oauth=SimpleNamespace(enabled=enabled, db_filename="oauth.sqlite3"),
        gateway=SimpleNamespace(enabled=enabled),
        encryption=SimpleNamespace(model_dump=lambda: {}),
    )

    def initialize(stage):
        async def run(*args, **kwargs):
            if fail_stage == stage or stage == "core":
                raise RuntimeError(f"{stage} failed")

        return AsyncMock(side_effect=run)

    audit = object()
    audit_close = AsyncMock(side_effect=lambda **kw: events.append("audit"))
    oauth = SimpleNamespace(
        initialize=initialize("oauth"),
        close=AsyncMock(side_effect=lambda: events.append("oauth")),
    )
    gateway_close = AsyncMock(side_effect=lambda: events.append("gateway"))
    monkeypatch.setattr("openviking.server.runtime.get_openviking_config", lambda: ov_config)
    monkeypatch.setattr("openviking.server.oauth.storage.OAuthStore", lambda path: oauth)
    monkeypatch.setattr("openviking.crypto.config.bootstrap_encryption", AsyncMock())
    monkeypatch.setattr(
        "openviking.server.gateway_host.embedded_gateway_config", lambda *a: ov_config.gateway
    )
    monkeypatch.setattr(
        "openviking_gateway.app.open_stores",
        lambda *a: (
            SimpleNamespace(initialize=initialize("kernel")),
            SimpleNamespace(initialize=initialize("management")),
            gateway_close,
        ),
    )
    monkeypatch.setattr(
        "openviking.observability.usage_audit.init_usage_audit_from_server_config",
        AsyncMock(return_value=audit if enabled else None),
    )
    monkeypatch.setattr("openviking.observability.usage_audit.shutdown_usage_audit", audit_close)
    service = SimpleNamespace(
        initialize=initialize("core"),
        close=AsyncMock(side_effect=lambda: events.append("service")),
    )
    app = None
    if fail_stage == "api":
        app = SimpleNamespace(
            state=SimpleNamespace(
                oauth_store=oauth,
                usage_audit_runtime=audit,
                gateway_app=SimpleNamespace(
                    state=SimpleNamespace(store=object(), management=object())
                ),
            )
        )
    runtime = ServerRuntime(service, config, app=app)
    with pytest.raises(RuntimeError, match="failed"):
        await runtime.start()
    assert events == (
        ["service"]
        if app is not None
        else ["service", "audit"]
        if not enabled
        else ["service", "oauth", "audit"]
        if fail_stage == "oauth"
        else ["service", "gateway", "oauth", "audit"]
    )
    assert runtime.stopped
    await runtime.stop()
    service.close.assert_awaited_once()
    if app is not None:
        assert runtime.shared.oauth_store is oauth
        assert runtime.shared.usage_audit_runtime is audit
        assert runtime.shared.gateway_store is app.state.gateway_app.state.store
        assert runtime.shared.gateway_management is app.state.gateway_app.state.management
        oauth.close.assert_not_awaited()
        audit_close.assert_not_awaited()
    else:
        audit_close.assert_awaited_once_with(runtime=audit if enabled else None)
    if not enabled or app is not None:
        oauth.initialize.assert_not_awaited()
        gateway_close.assert_not_awaited()


class _HealthTask:
    def __init__(self, done=False):
        self._done = done

    def done(self):
        return self._done


class _HealthThread:
    def __init__(self, alive=True):
        self._alive = alive

    def is_alive(self):
        return self._alive


def test_runtime_health_tracks_queue_worker_and_scheduler_loops(monkeypatch):
    tracker = SimpleNamespace(
        _cleanup_task=_HealthTask(), _cancel_poll_task=_HealthTask()
    )
    monkeypatch.setattr(
        "openviking.server.runtime.get_task_tracker", lambda: tracker
    )
    manager = SimpleNamespace(
        is_running=lambda: True, _queue_threads={"Semantic": _HealthThread()}
    )
    watch = SimpleNamespace(
        is_running=True, _leader_enabled=True, _loop_task=_HealthTask()
    )
    cleanup = SimpleNamespace(_running=True, _task=_HealthTask())
    service = SimpleNamespace(
        _queue_manager=manager,
        _watch_runtime=watch,
        _session_auto_commit_scheduler=None,
        _rule_cleanup_scheduler=cleanup,
        _compile_scheduler=None,
        _expiry_runtime=None,
    )
    runtime = ServerRuntime(service, ServerConfig(roles=["queue_worker", "scheduler"]))
    runtime.started = True
    assert runtime.is_healthy()
    manager._queue_threads["Semantic"]._alive = False
    assert not runtime.is_healthy()
    manager._queue_threads["Semantic"]._alive = True
    cleanup._task._done = True
    assert not runtime.is_healthy()


def test_runtime_health_requires_tracker_loops(monkeypatch):
    tracker = SimpleNamespace(
        _cleanup_task=_HealthTask(), _cancel_poll_task=_HealthTask(done=True)
    )
    monkeypatch.setattr(
        "openviking.server.runtime.get_task_tracker", lambda: tracker
    )
    runtime = ServerRuntime(SimpleNamespace(), ServerConfig(roles=["api"]))
    runtime.started = True
    assert not runtime.is_healthy()
