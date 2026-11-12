# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Operation-local acknowledgements for copies that can partially succeed."""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_copied_paths: ContextVar[set[str] | None] = ContextVar("agfs_copied_paths", default=None)


@contextmanager
def track_copy_progress(*, enabled: bool = True) -> Iterator[set[str]]:
    """Collect successful targets, including in asyncio.to_thread workers.

    The async AGFS adapter settles started I/O before propagating cancellation,
    so callers can inspect the collection as soon as the copy returns or raises.
    Nested operations and concurrent requests keep separate collections.
    """
    paths: set[str] = set()
    token = _copied_paths.set(paths if enabled else None)
    try:
        yield paths
    finally:
        _copied_paths.reset(token)


def record_copy_success(path: str) -> None:
    """Record only acknowledged writes or successfully prepared directories."""
    paths = _copied_paths.get()
    if paths is not None:
        paths.add(path.rstrip("/"))


def is_tracking_copy_progress() -> bool:
    """Whether primitive copy writes need worker-side acknowledgements."""
    return _copied_paths.get() is not None
