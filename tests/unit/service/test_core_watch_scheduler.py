# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for the ``enable_watch_scheduler`` gate in OpenVikingService.initialize().

The gate (core.py) starts the background WatchScheduler loop only when
``config.enable_watch_scheduler`` is true, so read-only replicas that share a
writer's data never run the periodic watch/refresh writes. In both cases the
scheduler instance is still created and wired for on-demand read paths.

These tests drive the real ``initialize()`` up to that branch, stubbing the
heavy storage / model dependencies so only the gate behaviour is exercised.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import openviking.service.core as core
from openviking.service.core import OpenVikingService
from openviking_cli.session.user_id import UserIdentifier


def _prime_service_for_initialize(monkeypatch, *, enable_watch_scheduler: bool):
    """Build an ``OpenVikingService`` whose ``initialize()`` reaches the watch gate.

    Returns the service, the (mock) WatchScheduler that ``initialize()`` creates,
    and the mock module logger so callers can assert on the emitted log line.
    """

    # The watch scheduler that initialize() constructs at the WatchScheduler(...)
    # call site. start() is async, so it must be awaitable.
    watch_scheduler = MagicMock(name="WatchScheduler")
    watch_scheduler.start = AsyncMock()

    # Stub every heavy dependency initialize() touches before the gate so that no
    # real storage / model / network work happens.
    monkeypatch.setattr(core, "get_openviking_config", lambda: MagicMock())
    monkeypatch.setattr(core, "init_context_collection", AsyncMock())
    monkeypatch.setattr(core, "init_viking_fs", lambda **kwargs: MagicMock())
    monkeypatch.setattr(core, "ResourceProcessor", lambda **kwargs: MagicMock())
    monkeypatch.setattr(core, "UserPrivacyConfigService", lambda *a, **k: MagicMock())
    monkeypatch.setattr(core, "SkillProcessor", lambda **kwargs: MagicMock())
    monkeypatch.setattr(core, "create_session_compressor", lambda **kwargs: MagicMock())
    monkeypatch.setattr(core, "WatchScheduler", lambda **kwargs: watch_scheduler)

    directory_initializer = MagicMock()
    directory_initializer.initialize_account_directories = AsyncMock(return_value=0)
    directory_initializer.initialize_user_directories = AsyncMock(return_value=0)
    monkeypatch.setattr(core, "DirectoryInitializer", lambda **kwargs: directory_initializer)

    # Do not register the throwaway service in the process-wide dependency global.
    monkeypatch.setattr("openviking.server.dependencies.set_service", lambda _svc: None)

    # Capture log lines without fighting the managed-logger propagation setup.
    mock_logger = MagicMock(name="logger")
    monkeypatch.setattr(core, "logger", mock_logger)

    service = OpenVikingService.__new__(OpenVikingService)
    service._initialized = False
    service._user = UserIdentifier.the_default_user()
    service._config = SimpleNamespace(enable_watch_scheduler=enable_watch_scheduler)

    # Pre-built infra so initialize() skips storage/embedder bootstrap and, with
    # no queue manager, the QueueFS blocks (including the ownership rebuild that
    # requires a running scheduler) are skipped.
    service._vikingdb_manager = MagicMock()
    service._embedder = MagicMock()
    service._agfs_client = MagicMock()
    service._encryptor = None
    service._queue_manager = None

    # Sub-services just record the dependency wiring calls.
    for attr in (
        "_fs_service",
        "_relation_service",
        "_pack_service",
        "_search_service",
        "_resource_service",
        "_session_service",
        "_resource_memory_link_service",
        "_debug_service",
    ):
        setattr(service, attr, MagicMock())

    # The data-dir lock is exercised elsewhere; keep it out of this test.
    monkeypatch.setattr(service, "_ensure_data_dir_lock_acquired", lambda: None)

    return service, watch_scheduler, mock_logger


@pytest.mark.asyncio
async def test_initialize_starts_watch_scheduler_when_enabled(monkeypatch):
    """Default config (enable_watch_scheduler=true) starts the background loop."""
    service, watch_scheduler, mock_logger = _prime_service_for_initialize(
        monkeypatch, enable_watch_scheduler=True
    )

    await service.initialize()

    watch_scheduler.start.assert_awaited_once()
    mock_logger.info.assert_any_call("WatchScheduler started")
    # The scheduler is still wired onto the service for on-demand read paths.
    assert service._watch_scheduler is watch_scheduler
    assert service._initialized is True


@pytest.mark.asyncio
async def test_initialize_skips_watch_scheduler_when_disabled(monkeypatch):
    """Read-only replicas (enable_watch_scheduler=false) never start the loop."""
    service, watch_scheduler, mock_logger = _prime_service_for_initialize(
        monkeypatch, enable_watch_scheduler=False
    )

    await service.initialize()

    watch_scheduler.start.assert_not_awaited()
    mock_logger.info.assert_any_call(
        "WatchScheduler disabled by config (enable_watch_scheduler=false)"
    )
    # The instance is still created and wired so on-demand read paths keep working.
    assert service._watch_scheduler is watch_scheduler
    assert service._initialized is True
