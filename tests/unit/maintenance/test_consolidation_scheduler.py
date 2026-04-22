# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for MemoryConsolidationScheduler (Phase B)."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.maintenance.consolidation_scheduler import (
    MemoryConsolidationScheduler,
    SchedulerGates,
    _default_system_context,
)


def _scheduler(
    *,
    consolidator_run=None,
    enumerate_scopes=None,
    gates=None,
    check_interval=0.05,
    scan_interval=0.05,
    max_concurrency=4,
):
    consolidator = MagicMock()
    consolidator.run = consolidator_run or AsyncMock()
    if enumerate_scopes is None:
        enumerate_scopes = AsyncMock(return_value=[])
    return MemoryConsolidationScheduler(
        consolidator=consolidator,
        enumerate_scopes=enumerate_scopes,
        gates=gates or SchedulerGates(),
        check_interval=check_interval,
        scan_interval=scan_interval,
        max_concurrency=max_concurrency,
    )


class TestConstructor:
    def test_rejects_zero_check_interval(self):
        with pytest.raises(ValueError):
            _scheduler(check_interval=0)

    def test_rejects_zero_scan_interval(self):
        with pytest.raises(ValueError):
            _scheduler(scan_interval=0)

    def test_rejects_zero_max_concurrency(self):
        with pytest.raises(ValueError):
            _scheduler(max_concurrency=0)


class TestSystemContext:
    def test_parses_account_from_agent_uri(self):
        ctx = _default_system_context("viking://agent/brianle/memories/patterns/")
        assert ctx.account_id == "brianle"
        assert ctx.user.user_id == "system"
        assert ctx.user.agent_id == "memory_consolidator"

    def test_parses_account_from_user_uri(self):
        ctx = _default_system_context("viking://user/alice/memories/preferences/")
        assert ctx.account_id == "alice"

    def test_unknown_scheme_falls_back_to_default(self):
        ctx = _default_system_context("viking://resources/repos/foo")
        assert ctx.account_id == "default"


class TestGates:
    def test_first_run_passes_with_no_history(self):
        s = _scheduler()
        assert s._gates_pass("viking://agent/x/memories/patterns/")

    def test_subsequent_run_blocked_by_time_gate(self):
        s = _scheduler(gates=SchedulerGates(min_hours_since_last=1.0))
        scope = "viking://agent/x/memories/patterns/"
        s._record_run(scope)
        # Just ran -- time gate should block.
        assert not s._gates_pass(scope)

    def test_subsequent_run_blocked_by_volume_gate(self):
        s = _scheduler(gates=SchedulerGates(min_hours_since_last=0.0, min_writes_since_last=5))
        scope = "viking://agent/x/memories/patterns/"
        s._record_run(scope)
        # Time gate is open (0h) but no writes since.
        assert not s._gates_pass(scope)

        s.record_writes(scope, 5)
        assert s._gates_pass(scope)

    def test_daily_cap(self):
        s = _scheduler(
            gates=SchedulerGates(
                min_hours_since_last=0.0,
                min_writes_since_last=0,
                max_runs_per_day=2,
            )
        )
        scope = "viking://agent/x/memories/patterns/"
        s._record_run(scope)
        s._record_run(scope)
        # Hit the cap -- third should be blocked.
        assert not s._gates_pass(scope)


class TestRecordWrites:
    def test_writes_accumulate(self):
        s = _scheduler()
        scope = "viking://agent/x/memories/patterns/"
        s.record_writes(scope, 3)
        s.record_writes(scope, 2)
        assert s._status[scope].last_seen_writes == 5

    def test_negative_writes_are_clamped_to_zero(self):
        s = _scheduler()
        scope = "viking://agent/x/memories/patterns/"
        s.record_writes(scope, -10)
        assert s._status[scope].last_seen_writes == 0


class TestRefreshScopes:
    @pytest.mark.asyncio
    async def test_caches_within_scan_interval(self):
        enumerate_scopes = AsyncMock(side_effect=[["a"], ["a", "b"]])
        s = _scheduler(enumerate_scopes=enumerate_scopes, scan_interval=10.0)
        first = await s._refresh_scopes()
        second = await s._refresh_scopes()
        assert first == ["a"]
        assert second == ["a"]
        enumerate_scopes.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_refreshes_after_scan_interval(self):
        enumerate_scopes = AsyncMock(side_effect=[["a"], ["a", "b"]])
        s = _scheduler(enumerate_scopes=enumerate_scopes, scan_interval=0.001)
        await s._refresh_scopes()
        await asyncio.sleep(0.01)
        second = await s._refresh_scopes()
        assert second == ["a", "b"]


class TestTriggerNow:
    @pytest.mark.asyncio
    async def test_runs_consolidator_immediately(self):
        consolidator_run = AsyncMock()
        s = _scheduler(consolidator_run=consolidator_run)
        ok = await s.trigger_now("viking://agent/x/memories/patterns/")
        assert ok is True
        consolidator_run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_if_already_executing(self):
        s = _scheduler()
        scope = "viking://agent/x/memories/patterns/"
        s._executing.add(scope)
        ok = await s.trigger_now(scope)
        assert ok is False
        s._consolidator.run.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_false_on_consolidator_failure(self):
        consolidator_run = AsyncMock(side_effect=RuntimeError("boom"))
        s = _scheduler(consolidator_run=consolidator_run)
        ok = await s.trigger_now("viking://agent/x/memories/patterns/")
        assert ok is False

    @pytest.mark.asyncio
    async def test_concurrent_trigger_now_only_runs_once(self):
        # Regression: race between pre-semaphore membership check and
        # in-semaphore set-add allowed two concurrent callers through.
        proceed = asyncio.Event()
        in_flight = asyncio.Event()

        async def slow_run(*args, **kwargs):
            in_flight.set()
            await proceed.wait()

        consolidator_run = AsyncMock(side_effect=slow_run)
        s = _scheduler(consolidator_run=consolidator_run, max_concurrency=4)
        scope = "viking://agent/x/memories/patterns/"

        first = asyncio.create_task(s.trigger_now(scope))
        await in_flight.wait()
        # Second caller arrives while first is in flight.
        second_ok = await s.trigger_now(scope)
        proceed.set()
        first_ok = await first

        assert first_ok is True
        assert second_ok is False
        assert consolidator_run.await_count == 1


class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_stop_roundtrip_runs_at_least_one_tick(self):
        consolidator_run = AsyncMock()
        enumerate_scopes = AsyncMock(return_value=["viking://agent/x/memories/patterns/"])
        s = _scheduler(
            consolidator_run=consolidator_run,
            enumerate_scopes=enumerate_scopes,
            check_interval=0.01,
            scan_interval=0.001,
        )
        await s.start()
        await asyncio.sleep(0.05)
        await s.stop()
        # Scope was new -- gates pass on first encounter, consolidator should fire.
        assert consolidator_run.await_count >= 1

    @pytest.mark.asyncio
    async def test_double_start_is_idempotent(self):
        s = _scheduler()
        await s.start()
        await s.start()  # no-op
        await s.stop()


class TestExecutingSetDeduping:
    @pytest.mark.asyncio
    async def test_same_scope_not_run_twice_concurrently(self):
        # Slow consolidator: hold the first run open while a second tick fires.
        in_flight = asyncio.Event()
        proceed = asyncio.Event()

        async def slow_run(*args, **kwargs):
            in_flight.set()
            await proceed.wait()

        consolidator_run = AsyncMock(side_effect=slow_run)
        enumerate_scopes = AsyncMock(return_value=["viking://agent/x/memories/patterns/"])
        s = _scheduler(
            consolidator_run=consolidator_run,
            enumerate_scopes=enumerate_scopes,
            check_interval=0.005,
            scan_interval=0.001,
        )
        await s.start()
        await in_flight.wait()
        # While first run is in flight, force a few more ticks.
        await asyncio.sleep(0.05)
        # Scope should still be in executing set (not yet released by finally).
        assert "viking://agent/x/memories/patterns/" in s._executing

        proceed.set()
        await asyncio.sleep(0.05)
        await s.stop()
        # Exactly one consolidator.run started (subsequent ticks deduped
        # by the executing set).
        assert consolidator_run.await_count == 1
