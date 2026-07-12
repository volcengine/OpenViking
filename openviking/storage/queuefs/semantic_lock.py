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
from openviking.storage.internal_names import MULTIWRITE_EXACT_LOCK_FILE_PREFIX
from openviking.storage.transaction.path_lock import LOCK_FILE_NAME
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

_TREE_LOCK_SUFFIX = f"/{LOCK_FILE_NAME}"
_EXACT_LOCK_TOKEN = f"/{MULTIWRITE_EXACT_LOCK_FILE_PREFIX}"


def _recoverable_tree_paths(lock_paths: Iterable[str]) -> list[str]:
    """Map handoff lock paths back to tree paths the worker can re-lock.

    The recovery path only knows ``acquire_tree`` / ``acquire_tree_batch``, so we
    must translate every entry in ``lock_paths`` into a directory path:

    - Tree-lock entries (``{dir}/.path.ovlock``): strip the suffix to recover ``{dir}``.
    - Exact-path-lock entries (``{dir}/.exact.ovlock.{name}.{digest}``): use the
      parent ``{dir}`` as a tree-lock target. This widens the lock from a single
      file to its containing directory, which is a strict superset and safe for
      semantic processing (the worker only needs visibility into the subtree).
      Without this branch, messages enqueued with ``acquire_exact_path`` lose
      all recovery options when the original ``LockHandle`` expires, causing the
      semantic queue to spin re-enqueueing the same messages forever (the
      classifier in ``semantic_processor`` treats ``LockAcquisitionError`` as
      retryable, with no max-attempts).

    Unknown / unrecognized entries are skipped. Returns deduped, order-preserved.
    """
    tree_paths: list[str] = []
    for lock_path in lock_paths:
        tree_path: Optional[str] = None
        if lock_path.endswith(_TREE_LOCK_SUFFIX):
            tree_path = lock_path[: -len(_TREE_LOCK_SUFFIX)] or "/"
        else:
            idx = lock_path.rfind(_EXACT_LOCK_TOKEN)
            # Skip lock paths that don't match either known pattern, or that
            # would collapse to root (idx == 0 means the file is at root, which
            # would acquire a tree lock over the whole namespace — never desired).
            if idx > 0:
                tree_path = lock_path[:idx]
        if tree_path and tree_path not in tree_paths:
            tree_paths.append(tree_path)
    return tree_paths


# Backwards-compat alias for callers that imported the old name.
_tree_paths_from_handoff = _recoverable_tree_paths


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
                tree_paths = _recoverable_tree_paths(lock_handoff.lock_paths)
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
