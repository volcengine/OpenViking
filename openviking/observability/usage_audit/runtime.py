# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Runtime bootstrap for product Usage/Audit projections."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openviking.observability.events import register_event_subscriber, unregister_event_subscriber
from openviking.server.config import ServerConfig
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.config.consts import DEFAULT_CONFIG_DIR

from .api_service import UsageAuditQueryService
from .inventory import ContextInventoryProvider
from .sqlite_store import SQLiteUsageAuditStore
from .store import UsageAuditStore
from .subscriber import UsageAuditSubscriber
from .worker import UsageAuditWorker

logger = logging.getLogger(__name__)

_SUBSCRIBER_NAME = "usage_audit"
_DEFAULT_SQLITE_RELATIVE_PATH = Path("_system") / "usage_audit" / "usage_audit.sqlite3"


@dataclass(slots=True)
class UsageAuditRuntime:
    """Container for active Usage/Audit runtime objects."""

    store: UsageAuditStore
    worker: UsageAuditWorker
    api_service: UsageAuditQueryService
    shutdown_flush_timeout_seconds: float

    async def delete_data(self, *, account_id: str, user_id: str | None = None) -> dict[str, int]:
        """Drain accepted events before purging one account/user scope's projections."""
        await self.worker.flush()
        return await self.store.delete_data(account_id=account_id, user_id=user_id)


def _resolve_sqlite_path(config: ServerConfig) -> Path:
    configured = config.observability.usage_audit.sqlite_path
    if configured:
        return Path(configured).expanduser().resolve()
    try:
        ov_config = get_openviking_config()
        workspace = Path(ov_config.storage.workspace).expanduser().resolve()
    except Exception:  # noqa: BLE001
        workspace = DEFAULT_CONFIG_DIR
    return workspace / _DEFAULT_SQLITE_RELATIVE_PATH


async def init_usage_audit_from_server_config(
    config: ServerConfig,
    *,
    app: Any = None,
    service: Any = None,
) -> UsageAuditRuntime | None:
    """Initialize Usage/Audit store, worker, and event subscriber."""
    usage_config = config.observability.usage_audit
    from openviking.observability.performance import configure_performance

    configure_performance(False)
    unregister_event_subscriber(_SUBSCRIBER_NAME)

    if not usage_config.enabled:
        if app is not None:
            app.state.usage_audit_runtime = None
        return None

    retention = {
        "performance_enabled": usage_config.performance_enabled,
        "performance_retention_days": usage_config.performance_retention_days,
        "usage_retention_days": usage_config.usage_retention_days,
        "audit_retention_days": usage_config.audit_retention_days,
        "audit_retention_per_account": usage_config.audit_retention_per_account,
    }
    store: UsageAuditStore
    if usage_config.backend == "postgres":
        from .postgres_store import PostgresUsageAuditStore

        pg = usage_config.postgres
        assert pg is not None  # Validated at the configuration boundary.
        store = PostgresUsageAuditStore(
            pg.dsn.get_secret_value(),
            schema=pg.schema_name,
            connect_timeout_seconds=pg.connect_timeout_seconds,
            statement_timeout_seconds=pg.statement_timeout_seconds,
            **retention,
        )
    else:
        store = SQLiteUsageAuditStore(_resolve_sqlite_path(config), **retention)
    await store.initialize()
    configure_performance(usage_config.performance_enabled)

    worker = UsageAuditWorker(
        store,
        queue_size=usage_config.queue_size,
        batch_size=usage_config.batch_size,
        flush_interval_seconds=usage_config.flush_interval_seconds,
    )
    await worker.start()
    register_event_subscriber(_SUBSCRIBER_NAME, UsageAuditSubscriber(worker))

    inventory = ContextInventoryProvider(
        service,
        ttl_seconds=usage_config.inventory_ttl_seconds,
    )
    api_service = UsageAuditQueryService(
        store=store,
        inventory=inventory,
        timezone_name=usage_config.timezone,
    )
    runtime = UsageAuditRuntime(
        store=store,
        worker=worker,
        api_service=api_service,
        shutdown_flush_timeout_seconds=usage_config.shutdown_flush_timeout_seconds,
    )
    if app is not None:
        app.state.usage_audit_runtime = runtime
    logger.info("Usage/Audit store initialized with %s backend", usage_config.backend)
    return runtime


async def shutdown_usage_audit(*, app: Any = None) -> None:
    """Shutdown Usage/Audit runtime if it is attached to the FastAPI app."""
    unregister_event_subscriber(_SUBSCRIBER_NAME)
    from openviking.observability.performance import configure_performance

    configure_performance(False)
    runtime = getattr(getattr(app, "state", None), "usage_audit_runtime", None)
    if runtime is None:
        return
    try:
        await runtime.worker.close(timeout_seconds=runtime.shutdown_flush_timeout_seconds)
    finally:
        await runtime.store.close()
        app.state.usage_audit_runtime = None


async def init_bot_usage_audit(config_path, *, app):
    """Opt an independent Bot process into the same configured projection owner.

    No second consumer is created by Compile or a model adapter. Off remains
    off. For cross-Pod reads use the explicit shared PostgreSQL configuration.
    """
    from openviking.server.config import load_server_config
    from openviking_cli.utils.config.config_loader import load_json_config

    data = load_json_config(Path(config_path))
    usage = data.get("server", {}).get("observability", {}).get("usage_audit", {})
    if not usage.get("performance_enabled", False):
        return None
    config = load_server_config(str(config_path))
    if not config.observability.usage_audit.performance_enabled:
        return None
    return await init_usage_audit_from_server_config(config, app=app)
