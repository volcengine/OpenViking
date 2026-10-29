# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""The Watch store contract shared by the file and MySQL implementations.

``current_run_id`` is the only run occupancy; every transition of it is a
compare-and-set on the stored row. Callers that change a Watch target, or start
occupying it, hold the target's PathLock around the call.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from openviking.resource.watch_types import (
    WatchAuthority,
    WatchPatch,
    WatchRun,
    WatchRunResult,
    WatchState,
    WatchTask,
)


def parent_uri(uri: str) -> Optional[str]:
    normalized = uri.rstrip("/")
    if "/" not in normalized:
        return None
    parent = normalized.rsplit("/", 1)[0]
    return None if parent in {"viking:/", "viking://"} else parent


def is_connector(auth_state: Optional[dict[str, Any]]) -> bool:
    from openviking.connector.delegate import ConnectorDelegate

    return ConnectorDelegate.is_watch_auth_state(auth_state)


def target_conflict_message(uri: Optional[str]) -> str:
    return (
        f"Target URI '{uri}' is already being monitored by an incompatible watch. "
        "Delete the conflicting watch or choose another target."
    )


@dataclass(frozen=True)
class WatchMoveResult:
    account_id: str
    from_uri: str
    to_uri: str
    task_ids: tuple[str, ...]


class WatchStore(ABC):
    """Persistent Watch rows and run occupancy."""

    @property
    @abstractmethod
    def provider(self) -> str: ...

    @abstractmethod
    async def initialize(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    # ── API reads and writes ────────────────────────────────────────────

    @abstractmethod
    async def get_state(self, task_id: str) -> Optional[WatchState]:
        """The row including occupancy, also while tombstoned."""

    @abstractmethod
    async def get_task(
        self, task_id: str, authority: WatchAuthority = WatchAuthority()
    ) -> Optional[WatchTask]: ...

    @abstractmethod
    async def list_tasks(
        self,
        authority: WatchAuthority,
        *,
        active_only: bool = False,
        scope_account_id: Optional[str] = None,
        target_uri: Optional[str] = None,
    ) -> list[WatchTask]:
        """Visible Watches; the file store keeps file order, MySQL lists newest first."""

    @abstractmethod
    async def create_task(self, task: WatchTask, *, initial_run_id: Optional[str] = None) -> None:
        """Insert a Watch, optionally already occupied by its first run.

        Raises ConflictError when the task ID exists or the target is already
        watched in the account, unless every Watch on it is a Connector Watch.
        """

    @abstractmethod
    async def patch_task(
        self,
        task_id: str,
        patch: WatchPatch,
        authority: WatchAuthority = WatchAuthority(),
        *,
        run_id: Optional[str] = None,
    ) -> Optional[WatchTask]:
        """Apply explicit fields. With *run_id*, only the occupying run may write.

        Changing ``watch_interval`` (or enabling a Watch without a schedule)
        reschedules it one interval after its last execution or creation.
        """

    @abstractmethod
    async def delete_task(self, task_id: str, authority: WatchAuthority) -> bool:
        """Tombstone a Watch; a row still occupied by a run is purged when it settles."""

    @abstractmethod
    async def delete_tasks(
        self, account_id: str, user_id: Optional[str] = None
    ) -> list[WatchState]:
        """Tombstone an identity's Watches and return those still occupied by a run."""

    # ── Run occupancy ───────────────────────────────────────────────────

    @abstractmethod
    async def due(self, limit: int, *, offset: int = 0) -> list[WatchState]:
        """Enabled idle Watches whose next run time has passed, earliest first."""

    @abstractmethod
    async def running(self, *, after: str = "", limit: int = 100) -> list[WatchState]:
        """Occupied rows ordered by task ID, starting after *after*."""

    @abstractmethod
    async def begin_run(
        self,
        task_id: str,
        *,
        run_id: str,
        target_uri: Optional[str],
        manual: bool = False,
    ) -> Optional[WatchRun]:
        """Begin *run_id* on an idle Watch whose target is still *target_uri*.

        Unless *manual*, the Watch must also be enabled and due. The caller
        holds that target's Tree PathLock.
        """

    @abstractmethod
    async def finish_run(self, task_id: str, run_id: str, result: WatchRunResult) -> bool:
        """Release occupancy and record the outcome iff *run_id* still occupies the row."""

    @abstractmethod
    async def fence_run(self, task_id: str, run_id: str) -> bool:
        """Stop admitting Task creation for *run_id*; whether it still occupies the row.

        Occupancy is kept: whoever releases it must first fence, then re-check
        that the run created no Task, so a concurrent creator that inserted
        its Task before the fence is never left unprotected.
        """

    @abstractmethod
    async def deactivate_idle(self, task_id: str, *, target_uri: Optional[str], error: str) -> bool:
        """Disable an idle Watch that still targets *target_uri* without starting a run."""

    @abstractmethod
    async def has_running_overlap(self, account_id: str, uris: Sequence[str]) -> bool:
        """Whether a running Watch target equals, contains or lies under any of *uris*."""

    # ── Target transfers (caller holds the transfer's PathLock) ─────────

    @abstractmethod
    async def move(
        self,
        *,
        account_id: str,
        from_uri: str,
        to_uri: str,
        task_ids: Optional[Sequence[str]] = None,
    ) -> WatchMoveResult:
        """Atomically move Watch targets and return the exact moved task IDs.

        With ``task_ids=None``, all live Watches at or below ``from_uri`` move.
        An explicit list moves only those tasks and fails unless every task is
        still live, owned by the account and under ``from_uri``. An empty list
        is a no-op. The caller holds the transfer's PathLock.
        """

    @abstractmethod
    async def deactivate_under_uri(self, account_id: str, uri: str) -> list[WatchTask]: ...
