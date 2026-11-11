# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Semantic queue lock resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from openviking.storage.pathlock_recovery import adopt_or_reacquire
from openviking.storage.pathlock_transfer import PathLockGuard
from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SemanticLockScope:
    """Resolved lock scope for one semantic message."""

    lock: Optional[Dict[str, Any]]  # lease dict or None
    _owned: bool = False  # True when this scope owns the lease (must release)

    @classmethod
    async def resolve(
        cls,
        lock_handoff: Optional[Dict[str, Any]],
        *,
        caller_lock: Optional[Dict[str, Any]] = None,
        fallback_path_factory: Optional[Callable[[], str]] = None,
        ctx: Any = None,
    ) -> "SemanticLockScope":
        """Resolve a live lock, reacquiring its original coverage when stale."""
        if lock_handoff and caller_lock is not None:
            raise ValueError("semantic lock must come from either message or caller, not both")
        if caller_lock is not None:
            viking_fs = get_viking_fs()
            return cls(await viking_fs._async_agfs.pathlock_as_borrowed(caller_lock), _owned=False)
        if lock_handoff:
            viking_fs = get_viking_fs()

            async def requests():
                path = fallback_path_factory()
                # File semantic plans can legitimately carry an Exact lease.
                # Preserve its kind rather than escalating it to a Tree lease.
                kind = next(
                    (
                        item.get("kind")
                        for item in lock_handoff.get("covered_paths") or []
                        if isinstance(item, dict) and item.get("path") == path
                    ),
                    "tree",
                )
                if not lock_handoff.get("covered_paths"):
                    logger.warning(
                        "Recovering historic semantic handoff with conservative Tree coverage path=%s",
                        path,
                    )
                return [{"path": path, "kind": kind}]

            lease = await adopt_or_reacquire(
                viking_fs._async_agfs,
                lock_handoff,
                ctx=ctx,
                fallback_requests=requests if fallback_path_factory else None,
            )
            return cls(lease, _owned=True)
        return cls(None)

    async def close(self) -> None:
        """Release the owned lock lease if held."""
        if self.lock is not None and self._owned:
            guard = PathLockGuard(get_viking_fs()._async_agfs, self.lock)
            try:
                await guard.close()
            finally:
                self.lock = guard.lease
                self._owned = self.lock is not None
