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


@dataclass(slots=True)
class UsageAuditRuntime:
    """Container for active Usage/Audit runtime objects."""

    store: UsageAuditStore
    worker: UsageAuditWorker
    api_service: UsageAuditQueryService
    shutdown_flush_timeout_seconds: float


def _resolve_sqlite_path(config: ServerConfig) -> Path:
    configured = config.observability.usage_audit.sqlite_path
    if configured:
        return Path(configured).expanduser().resolve()
    try:
        ov_config = get_openviking_config()
        workspace = Path(ov_config.storage.workspace).expanduser().resolve()
    except Exception:  # noqa: BLE001
        workspace = DEFAULT_CONFIG_DIR
    return workspace / "usage_audit.sqlite3"


async def init_usage_audit_from_server_config(
    config: ServerConfig,
    *,
    app: Any = None,
    service: Any = None,
) -> UsageAuditRuntime | None:
    """Initialize Usage/Audit store, worker, and event subscriber."""
    usage_config = config.observability.usage_audit
    unregister_event_subscriber(_SUBSCRIBER_NAME)

    if not usage_config.enabled:
        if app is not None:
            app.state.usage_audit_runtime = None
        return None

    store = SQLiteUsageAuditStore(
        _resolve_sqlite_path(config),
        usage_retention_days=usage_config.usage_retention_days,
        audit_retention_days=usage_config.audit_retention_days,
        audit_retention_per_account=usage_config.audit_retention_per_account,
        timezone_name=usage_config.timezone,
    )
    await store.initialize()

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
    logger.info("Usage/Audit store initialized with sqlite backend")
    return runtime


async def shutdown_usage_audit(*, app: Any = None) -> None:
    """Shutdown Usage/Audit runtime if it is attached to the FastAPI app."""
    unregister_event_subscriber(_SUBSCRIBER_NAME)
    runtime = getattr(getattr(app, "state", None), "usage_audit_runtime", None)
    if runtime is None:
        return
    try:
        await runtime.worker.close(timeout_seconds=runtime.shutdown_flush_timeout_seconds)
    finally:
        await runtime.store.close()
        app.state.usage_audit_runtime = None
