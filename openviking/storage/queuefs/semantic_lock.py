# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Semantic queue lock resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from openviking.storage.errors import LockAcquisitionError
from openviking.storage.transaction import (
    NO_LOCK,
    LockHandoffRef,
    LockLease,
    OwnedLockLease,
    get_lock_manager,
)
from openviking.storage.transaction.path_lock import LOCK_FILE_NAME
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

_TREE_LOCK_SUFFIX = f"/{LOCK_FILE_NAME}"


def _tree_paths_from_handoff(lock_paths: Iterable[str]) -> list[str]:
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

    lock: LockLease

    @classmethod
    async def resolve(
        cls,
        lock_handoff: Optional[LockHandoffRef],
        *,
        caller_lock: LockLease = NO_LOCK,
    ) -> "SemanticLockScope":
        if lock_handoff and caller_lock.active:
            raise ValueError("semantic lock must come from either message or caller, not both")
        if caller_lock is not NO_LOCK and not caller_lock.active:
            raise ValueError("caller semantic lock is inactive")
        if caller_lock.active:
            return cls(caller_lock.as_borrowed())
        if lock_handoff:
            manager = get_lock_manager()
            try:
                return cls(await OwnedLockLease.from_handoff(lock_handoff, manager=manager))
            except LockAcquisitionError as exc:
                tree_paths = _tree_paths_from_handoff(lock_handoff.lock_paths)
                if not tree_paths:
                    raise

                handle = manager.create_handle()
                if len(tree_paths) == 1:
                    acquired = await manager.acquire_tree(handle, tree_paths[0])
                else:
                    acquired = await manager.acquire_tree_batch(handle, tree_paths)
                if not acquired:
                    await manager.release(handle)
                    raise LockAcquisitionError(
                        f"Failed to reacquire semantic lock for {tree_paths}"
                    ) from exc

                logger.info(
                    "Recovered semantic lock handoff %s by reacquiring %s",
                    lock_handoff.handle_id,
                    tree_paths,
                )
                return cls(OwnedLockLease.from_handle(manager, handle))
        return cls(NO_LOCK)

    async def close(self) -> None:
        await self.lock.close()
