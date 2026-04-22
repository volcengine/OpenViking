# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Background scheduler that drives MemoryConsolidator on a cadence.

Modeled on openviking/resource/watch_scheduler.py. Per-scope gates mirror
Claude Code dream's autoDream.ts gate chain (24h time gate, scan-throttle,
volume gate). Scopes are enumerated per-account from the vector index.

Phase B of the OV memory consolidation rollout. Phase A's
MemoryConsolidator.run() is the inner unit; this layer just decides when
to call it.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from openviking.maintenance.memory_consolidator import MemoryConsolidator
from openviking.server.identity import RequestContext, Role, UserIdentifier
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


@dataclass
class SchedulerGates:
    """Per-scope gating thresholds. Mirrors dream's gate-chain knobs."""

    min_hours_since_last: float = 24.0
    min_writes_since_last: int = 5
    max_runs_per_day: int = 4


@dataclass
class ScopeStatus:
    """Lightweight per-scope cadence state held in memory.

    last_run_at uses monotonic seconds because the time gate is a
    process-lifetime concept (we only care about elapsed time within
    this scheduler instance). runs_today_window_start uses wall-clock
    seconds because the daily cap is a calendar-day concept that
    callers and audit logs reason about in real time.
    """

    scope_uri: str
    last_run_at: Optional[float] = None  # monotonic seconds
    runs_today: int = 0
    runs_today_window_start: Optional[float] = None  # wall-clock seconds
    last_seen_writes: int = 0


# Mirrors dream's SESSION_SCAN_INTERVAL_MS = 10*60*1000 (autoDream.ts:144).
DEFAULT_SCAN_INTERVAL_SECONDS = 600.0
DEFAULT_CHECK_INTERVAL_SECONDS = 60.0
DEFAULT_MAX_CONCURRENCY = 4


class MemoryConsolidationScheduler:
    """Background loop that consolidates memory scopes on a cadence.

    Lifecycle: start() spawns the asyncio task that wakes every
    check_interval, walks the scope list (re-enumerated at most every
    SCAN_INTERVAL), and runs the consolidator on scopes whose gates
    pass and that are not already executing.

    Thread-safe by virtue of asyncio: all state is touched only from
    the scheduler task.
    """

    def __init__(
        self,
        consolidator: MemoryConsolidator,
        enumerate_scopes: Callable[[], Awaitable[List[str]]],
        *,
        gates: Optional[SchedulerGates] = None,
        check_interval: float = DEFAULT_CHECK_INTERVAL_SECONDS,
        scan_interval: float = DEFAULT_SCAN_INTERVAL_SECONDS,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        build_ctx: Optional[Callable[[str], RequestContext]] = None,
    ):
        """Initialize the scheduler.

        Args:
            consolidator: MemoryConsolidator instance to drive.
            enumerate_scopes: async callable returning the current list
                of scope URIs to consider. Called at most once per
                scan_interval. Caller decides the scope source (e.g.
                walk known accounts/users/agents/categories).
            gates: per-scope thresholds. Same defaults for all scopes
                unless caller plugs in a per-scope override layer.
            check_interval: seconds between scheduler ticks.
            scan_interval: minimum seconds between scope enumerations.
                Saves repeated FS/index walks on a busy loop.
            max_concurrency: max parallel consolidations.
            build_ctx: callable that produces a RequestContext for a
                given scope URI. Defaults to a system identity inferred
                from the URI (account_id from viking://agent/<acct>/...).
        """
        if check_interval <= 0:
            raise ValueError("check_interval must be > 0")
        if scan_interval <= 0:
            raise ValueError("scan_interval must be > 0")
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be > 0")

        self._consolidator = consolidator
        self._enumerate_scopes = enumerate_scopes
        self._gates = gates or SchedulerGates()
        self._check_interval = check_interval
        self._scan_interval = scan_interval
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._build_ctx = build_ctx or _default_system_context

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._executing: Set[str] = set()
        self._last_scan_at: float = 0.0
        self._cached_scopes: List[str] = []
        self._status: Dict[str, ScopeStatus] = {}

    @property
    def gates(self) -> SchedulerGates:
        return self._gates

    @property
    def status_snapshot(self) -> Dict[str, ScopeStatus]:
        """Return a shallow copy of the per-scope status table."""
        return dict(self._status)

    async def start(self) -> None:
        """Start the background loop."""
        if self._running:
            logger.warning("[MemoryConsolidationScheduler] already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            f"[MemoryConsolidationScheduler] started: check={self._check_interval}s "
            f"scan={self._scan_interval}s max_concurrent={self._semaphore._value}"
        )

    async def stop(self) -> None:
        """Cancel the background loop and wait for it to exit."""
        if not self._running:
            return
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("[MemoryConsolidationScheduler] stopped")

    async def trigger_now(self, scope_uri: str) -> bool:
        """Force a single scope through the consolidator immediately.

        Bypasses scan_interval and time gates but still respects
        executing-set deduping and max_concurrency.
        """
        async with self._semaphore:
            # Re-check after semaphore acquire to close the race where
            # two callers passed the pre-acquire check before either
            # added itself to _executing.
            if scope_uri in self._executing:
                logger.info(f"[MemoryConsolidationScheduler] {scope_uri} already executing")
                return False
            self._executing.add(scope_uri)
            try:
                ctx = self._build_ctx(scope_uri)
                await self._consolidator.run(scope_uri, ctx)
                self._record_run(scope_uri)
                return True
            except Exception as e:
                logger.warning(
                    f"[MemoryConsolidationScheduler] trigger_now failed for {scope_uri}: {e}"
                )
                return False
            finally:
                self._executing.discard(scope_uri)

    async def _loop(self) -> None:
        """Tick loop. Sleeps check_interval between iterations."""
        logger.info("[MemoryConsolidationScheduler] loop started")
        try:
            while self._running:
                try:
                    await self._tick()
                except Exception as e:
                    logger.exception(f"[MemoryConsolidationScheduler] tick failed: {e}")
                try:
                    await asyncio.sleep(self._check_interval)
                except asyncio.CancelledError:
                    break
        finally:
            logger.info("[MemoryConsolidationScheduler] loop ended")

    async def _tick(self) -> None:
        scopes = await self._refresh_scopes()
        if not scopes:
            return

        runs: List[asyncio.Task] = []
        for scope in scopes:
            if scope in self._executing:
                continue
            if not self._gates_pass(scope):
                continue
            runs.append(asyncio.create_task(self._run_scope(scope)))

        if runs:
            await asyncio.gather(*runs, return_exceptions=True)

    async def _refresh_scopes(self) -> List[str]:
        """Re-enumerate scopes at most once per scan_interval."""
        now = time.monotonic()
        if now - self._last_scan_at < self._scan_interval and self._cached_scopes:
            return self._cached_scopes
        try:
            self._cached_scopes = await self._enumerate_scopes()
            self._last_scan_at = now
        except Exception as e:
            logger.warning(f"[MemoryConsolidationScheduler] enumerate_scopes failed: {e}")
        return self._cached_scopes

    def _gates_pass(self, scope_uri: str) -> bool:
        """Decide whether scope is due for consolidation right now.

        Time gate: at least min_hours_since_last hours since last run.
        Volume gate: not yet enforced here -- caller wires write-counts
        into _status via record_writes(); v1 falls back to time gate
        only if volume hasn't been recorded.
        Daily cap: refuse if runs_today >= max_runs_per_day inside the
        24h sliding window.
        """
        now_mono = time.monotonic()
        now_wall = time.time()
        st = self._status.setdefault(scope_uri, ScopeStatus(scope_uri=scope_uri))

        if st.runs_today_window_start is not None:
            if now_wall - st.runs_today_window_start >= 86400.0:
                st.runs_today = 0
                st.runs_today_window_start = now_wall
        if st.runs_today >= self._gates.max_runs_per_day:
            return False

        if st.last_run_at is not None:
            elapsed_hours = (now_mono - st.last_run_at) / 3600.0
            if elapsed_hours < self._gates.min_hours_since_last:
                return False

        if st.last_seen_writes < self._gates.min_writes_since_last:
            # First-ever run for a scope is allowed (last_run_at is None
            # AND last_seen_writes default 0); we let it through so a
            # cold start can backfill. After that, require new writes.
            if st.last_run_at is not None:
                return False

        return True

    async def _run_scope(self, scope_uri: str) -> None:
        async with self._semaphore:
            if scope_uri in self._executing:
                return
            self._executing.add(scope_uri)
            try:
                ctx = self._build_ctx(scope_uri)
                await self._consolidator.run(scope_uri, ctx)
                self._record_run(scope_uri)
            except Exception as e:
                logger.warning(
                    f"[MemoryConsolidationScheduler] consolidate failed for {scope_uri}: {e}"
                )
            finally:
                self._executing.discard(scope_uri)

    def _record_run(self, scope_uri: str) -> None:
        st = self._status.setdefault(scope_uri, ScopeStatus(scope_uri=scope_uri))
        st.last_run_at = time.monotonic()
        st.runs_today += 1
        if st.runs_today_window_start is None:
            st.runs_today_window_start = time.time()
        st.last_seen_writes = 0

    def record_writes(self, scope_uri: str, writes: int) -> None:
        """External signal: N new writes have happened in this scope.

        Callers (e.g. memory write hooks) bump the counter so the
        volume gate can fire when the scope accumulates enough churn.
        """
        st = self._status.setdefault(scope_uri, ScopeStatus(scope_uri=scope_uri))
        st.last_seen_writes += max(0, writes)


def _default_system_context(scope_uri: str) -> RequestContext:
    """Build a system RequestContext from a scope URI.

    Parses account_id from viking://agent/<acct>/... or
    viking://user/<user>/... patterns. Falls back to "default".
    """
    account_id = "default"
    if scope_uri.startswith("viking://agent/"):
        parts = scope_uri[len("viking://agent/"):].split("/", 1)
        if parts and parts[0]:
            account_id = parts[0]
    elif scope_uri.startswith("viking://user/"):
        parts = scope_uri[len("viking://user/"):].split("/", 1)
        if parts and parts[0]:
            account_id = parts[0]

    user = UserIdentifier(
        account_id=account_id,
        user_id="system",
        agent_id="memory_consolidator",
    )
    return RequestContext(user=user, role=Role.ROOT)
