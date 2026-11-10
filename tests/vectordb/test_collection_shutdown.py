# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Exercise real persistent collections in subprocesses so a deadlock is bounded."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("cycle", [False, True])
def test_finalizer_inside_logging_handler(tmp_path, cycle):
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), str(tmp_path), str(int(cycle))],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "reopened persisted data" in result.stdout


def _run(path, cycle):
    import faulthandler
    import gc
    import logging
    import threading

    from openviking.storage.vectordb.collection import collection as collection_module
    from openviking.storage.vectordb.collection.local_collection import (
        get_or_create_local_collection,
    )

    faulthandler.dump_traceback_later(15, exit=True)
    meta = {
        "CollectionName": "shutdown",
        "Fields": [
            {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
            {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
        ],
    }
    col = get_or_create_local_collection(meta, path=path)
    col.create_index("idx", {"IndexName": "idx", "VectorIndex": {"IndexType": "flat"}})
    col.upsert_data([{"id": 1, "vector": [0.1] * 4}])
    inner = col._Collection__collection
    scheduler = inner.scheduler
    scheduler_thread = scheduler._thread
    ready = threading.Event()
    armed = threading.Event()
    if cycle:
        gc.disable()
        col.cycle = col
    box = [col]
    del col

    class SchedulerFilter(logging.Filter):
        def filter(self, record):
            if armed.is_set() and record.thread == scheduler_thread.ident:
                ready.set()
            return True

    class Handler(logging.Handler):
        def emit(self, record):
            if record.msg != "trigger":
                return
            armed.set()
            scheduler.wakeup()
            assert ready.wait(5), "scheduler did not reach logging"
            # The handler RLock is held here. The scheduler is about to acquire
            # it too. Both refcount and cyclic finalization must return without
            # joining it, persisting indexes, logging, or taking application locks.
            box.clear()
            gc.collect()

    handler = Handler()
    log = logging.getLogger("collection-shutdown-regression")
    log.setLevel(logging.DEBUG)
    log.propagate = False
    log.addHandler(handler)
    log.addFilter(SchedulerFilter())
    scheduler._logger = log
    log.info("trigger")
    log.removeHandler(handler)
    # Drain from normal lifecycle code only after the handler has released its
    # lock. No mock Collection, scheduler, vector index, or persistence layer.
    collection_module.close_unclosed_collections()
    assert not scheduler_thread.is_alive()
    assert inner.scheduler is None
    assert not inner.indexes.get_all()
    reopened = get_or_create_local_collection(path=path)
    try:
        assert reopened.fetch_data([1]).items[0].id == 1
        assert len(reopened.search_by_vector("idx", [0.1] * 4, limit=5).data) == 1
    finally:
        reopened.close()
    print("reopened persisted data", flush=True)
    faulthandler.cancel_dump_traceback_later()


def test_close_waits_for_maintenance_outside_barrier(tmp_path):
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), str(tmp_path), "maintenance"],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _run_maintenance(path):
    import faulthandler
    import threading
    import time

    from openviking.storage.vectordb.collection.local_collection import (
        get_or_create_local_collection,
    )

    faulthandler.dump_traceback_later(15, exit=True)
    col = get_or_create_local_collection(
        {
            "CollectionName": "active_job",
            "Fields": [{"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True}],
        },
        path=path,
    )
    inner = col._Collection__collection
    scheduler_thread = inner.scheduler._thread
    entered, release, published, closed = (threading.Event() for _ in range(4))

    def rebuild():
        entered.set()
        assert release.wait(5)
        with inner._index_mutation_barrier.exclusive_mutation():
            assert inner.store_mgr is not None
            published.set()

    inner._rebuild_indexes_if_needed = rebuild
    inner.scheduler.add_job(inner._register_index_manage_job, "date")
    assert entered.wait(5)

    def close():
        col.close()
        closed.set()

    closer = threading.Thread(target=close)
    closer.start()
    deadline = time.monotonic() + 5
    while not inner._closing and time.monotonic() < deadline:
        time.sleep(0.001)
    assert inner._closing
    assert not closed.is_set()
    assert inner.store_mgr is not None
    release.set()
    closer.join(5)
    assert published.is_set() and closed.is_set()
    assert not scheduler_thread.is_alive()
    assert inner.scheduler is None
    assert inner.store_mgr is None
    col.close()  # explicit close is idempotent
    faulthandler.cancel_dump_traceback_later()


def test_deferred_close_retries_failed_backend():
    from unittest.mock import Mock

    from openviking.storage.vectordb.collection.collection import (
        Collection,
        ICollection,
        close_unclosed_collections,
    )

    backend = Mock(spec=ICollection)
    backend.close.side_effect = [RuntimeError("temporary close failure"), None]
    wrapper = Collection(backend)
    del wrapper
    backend.close.assert_not_called()
    with pytest.raises(RuntimeError, match="Failed to close abandoned"):
        close_unclosed_collections()
    close_unclosed_collections()
    close_unclosed_collections()
    assert backend.close.call_count == 2


if __name__ == "__main__":
    if sys.argv[2] == "maintenance":
        _run_maintenance(sys.argv[1])
    else:
        _run(sys.argv[1], bool(int(sys.argv[2])))
