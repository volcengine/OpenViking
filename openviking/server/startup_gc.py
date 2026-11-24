# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Opt-in startup GC freezing for one long-lived server per process."""

import gc
from contextlib import contextmanager
from threading import Lock

_owner = Lock()


@contextmanager
def startup_gc_freeze(enabled: bool):
    """Freeze initialization objects and unfreeze before lifespan cleanup.

    GC's permanent generation is process-global. Reject overlapping owners or
    an existing external freeze rather than taking ownership of their objects.
    New allocations keep participating in ordinary automatic collection.
    """
    if not enabled:
        yield
        return
    if not _owner.acquire(blocking=False):
        raise RuntimeError("Startup GC freeze requires one active server lifespan per process")
    frozen = False
    try:
        if gc.get_freeze_count():
            raise RuntimeError("Startup GC freeze cannot own an existing frozen generation")
        gc.collect()
        gc.freeze()
        frozen = True
        yield
    finally:
        if frozen:
            gc.unfreeze()
        _owner.release()
