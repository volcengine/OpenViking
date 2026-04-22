# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared fixtures for maintenance unit tests."""

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from openviking.maintenance.memory_consolidator import MemoryConsolidator
from openviking.session.memory_archiver import ArchivalResult
from openviking.session.memory_deduplicator import (
    ClusterDecision,
    ClusterDecisionType,
)


def make_request_ctx(account_id: str = "test-account") -> MagicMock:
    """Build a mock RequestContext with the given account_id."""
    ctx = MagicMock()
    ctx.account_id = account_id
    return ctx


@asynccontextmanager
async def noop_lock(*args: Any, **kwargs: Any):
    """Async context manager replacement for LockContext in tests."""
    yield None


def make_consolidator(
    *,
    archive_candidates: list | None = None,
    cluster_decision: ClusterDecision | None = None,
    write_succeeds: bool = True,
    delete_succeeds: bool = True,
    search_results: Any = None,
    with_service: bool = True,
) -> MemoryConsolidator:
    """Build a MemoryConsolidator with all dependencies mocked.

    Defaults are intentionally inert (no clusters, no archive, no LLM
    decision) so callers only override what their test exercises.
    """
    vikingdb = MagicMock()
    viking_fs = MagicMock()
    viking_fs._uri_to_path = MagicMock(return_value="/fake/path")
    viking_fs.exists = AsyncMock(return_value=False)
    viking_fs.read = AsyncMock(return_value="memory body")
    viking_fs.write = (
        AsyncMock() if write_succeeds else AsyncMock(side_effect=RuntimeError("write boom"))
    )
    viking_fs.rm = (
        AsyncMock() if delete_succeeds else AsyncMock(side_effect=RuntimeError("del boom"))
    )
    viking_fs.mkdir = AsyncMock()

    dedup = MagicMock()
    dedup.consolidate_cluster = AsyncMock(
        return_value=cluster_decision
        or ClusterDecision(
            decision=ClusterDecisionType.KEEP_ALL,
            cluster=[],
            reason="test default",
        )
    )

    archiver = MagicMock()
    archiver.scan = AsyncMock(return_value=archive_candidates or [])
    archiver.archive = AsyncMock(
        return_value=ArchivalResult(scanned=0, archived=0, skipped=0, errors=0)
    )

    service = None
    if with_service:
        service = MagicMock()
        service.search = MagicMock()
        if search_results is None:
            service.search.search = AsyncMock(return_value={"memories": []})
        elif callable(search_results):
            service.search.search = AsyncMock(side_effect=search_results)
        else:
            service.search.search = AsyncMock(return_value=search_results)

    consolidator = MemoryConsolidator(
        vikingdb=vikingdb,
        viking_fs=viking_fs,
        dedup=dedup,
        archiver=archiver,
        service=service,
    )
    consolidator._cluster_scope = AsyncMock(return_value=[])
    return consolidator
