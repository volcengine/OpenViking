# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Semantic queue lock resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Optional

from openviking.storage.errors import LockAcquisitionError
from openviking.storage.internal_names import MULTIWRITE_PATH_LOCK_FILE
from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

_TREE_LOCK_SUFFIX = f"/{MULTIWRITE_PATH_LOCK_FILE}"


def _tree_paths_from_handoff(lock_paths: Iterable[str]) -> list[str]:
    """Extract tree lock directory paths from legacy lock file handles."""
    tree_paths: list[str] = []
    for lock_path in lock_paths:
        if not lock_path.endswith(_TREE_LOCK_SUFFIX):
            continue
        tree_path = lock_path[: -len(_TREE_LOCK_SUFFIX)] or "/"
        if tree_path not in tree_paths:
            tree_paths.append(tree_path)
    return tree_paths


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
    ) -> "SemanticLockScope":
        """Resolve a live lock, reacquiring its original coverage when stale."""
        if lock_handoff and caller_lock is not None:
            raise ValueError("semantic lock must come from either message or caller, not both")
        if caller_lock is not None:
            viking_fs = get_viking_fs()
            return cls(await viking_fs._async_agfs.pathlock_as_borrowed(caller_lock), _owned=False)
        if lock_handoff:
            viking_fs = get_viking_fs()
            try:
                return cls(await viking_fs._async_agfs.pathlock_adopt(lock_handoff), _owned=True)
            except LockAcquisitionError as error:
                # ponytail: Replace this message check with a recoverable handoff
                # exception when the native binding exposes typed handoff failures.
                error_message = str(error)
                if "is no longer owned by" not in error_message and (
                    "logical-path provider handoff requires covered_paths" not in error_message
                ):
                    raise
                covered_paths = lock_handoff.get("covered_paths") or []
                if not covered_paths:
                    tree_paths = _tree_paths_from_handoff(lock_handoff["lock_paths"])
                    if not tree_paths and fallback_path_factory:
                        tree_paths = [fallback_path_factory()]
                    if not tree_paths:
                        raise
                    covered_paths = [{"path": path, "kind": "tree"} for path in tree_paths]

                lease = await viking_fs._async_agfs.pathlock_acquire_batch(covered_paths)

                logger.info(
                    "Recovered semantic lock handoff %s by reacquiring %s",
                    lock_handoff.get("owner_id"),
                    covered_paths,
                )
                return cls(lease, _owned=True)
        return cls(None)

    async def close(self) -> None:
        """Release the owned lock lease if held."""
        if self.lock is not None and self._owned:
            await get_viking_fs()._async_agfs.pathlock_release(self.lock)
