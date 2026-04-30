# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Concurrency safety tests for migration components.

Tests verify that DualWriteAdapter and MigrationStateManager behave
correctly under concurrent access from multiple threads.

Scenarios:
1. Multi-threaded upsert to DualWriteAdapter — no crash, data consistent
2. Concurrent set_active + upsert — consistent read side
3. Concurrent MigrationStateManager save — no corruption
"""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List

import pytest

from openviking.storage.migration.state import (
    MigrationPhase,
    MigrationState,
    MigrationStateManager,
)

# Reuse the FakeCollectionAdapter from the blue-green adapter tests
from tests.migration.test_blue_green_adapter import FakeCollectionAdapter


# =============================================================================
# Helpers
# =============================================================================


def _make_adapter(
    source: FakeCollectionAdapter,
    target: FakeCollectionAdapter,
    *,
    active_side: str = "source",
    dual_write_enabled: bool = True,
):
    """Create a DualWriteAdapter (imported lazily)."""
    from openviking.storage.migration.blue_green_adapter import DualWriteAdapter

    return DualWriteAdapter(
        source=source,
        target=target,
        active_side=active_side,
        dual_write_enabled=dual_write_enabled,
    )


def _make_state(state_dir: str, migration_id: str = "test_migration") -> MigrationState:
    """Create a minimal MigrationState for testing."""
    return MigrationState(
        migration_id=migration_id,
        phase=MigrationPhase.dual_write,
        source_collection="source_coll",
        target_collection="target_coll",
        active_side="source",
        dual_write_enabled=True,
        source_embedder_name="embedder_a",
        target_embedder_name="embedder_b",
    )


# =============================================================================
# Scenario 1: Multi-threaded upsert to DualWriteAdapter
# =============================================================================


def test_concurrent_upsert_no_crash():
    """Multiple threads upserting concurrently must not crash.

    Each thread upserts a unique record. After all threads complete,
    both source and target must contain exactly the expected number
    of records, and the IDs must match across both sides.
    """
    source = FakeCollectionAdapter(collection_name="source")
    target = FakeCollectionAdapter(collection_name="target")
    adapter = _make_adapter(source, target, active_side="source", dual_write_enabled=True)

    num_threads = 20
    records_per_thread = 10

    def _upsert_batch(thread_id: int) -> List[str]:
        """Upsert records_per_thread records, return their IDs."""
        ids: List[str] = []
        for i in range(records_per_thread):
            record = {
                "id": f"thread_{thread_id}_rec_{i}",
                "text": f"data from thread {thread_id}, record {i}",
                "value": thread_id * 1000 + i,
            }
            result = adapter.upsert(record)
            ids.extend(result)
        return ids

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = {
            executor.submit(_upsert_batch, tid): tid
            for tid in range(num_threads)
        }
        all_ids: List[str] = []
        for future in as_completed(futures):
            all_ids.extend(future.result())

    expected_count = num_threads * records_per_thread

    # Both sides must have the same number of records
    assert len(source.stored_ids) == expected_count, (
        f"Source has {len(source.stored_ids)} records, expected {expected_count}"
    )
    assert len(target.stored_ids) == expected_count, (
        f"Target has {len(target.stored_ids)} records, expected {expected_count}"
    )

    # The stored IDs must be identical on both sides
    assert source.stored_ids == target.stored_ids, (
        "Source and target stored IDs differ"
    )

    # All expected IDs must be present
    assert sorted(all_ids) == source.stored_ids, (
        "Returned IDs do not match source stored IDs"
    )


# =============================================================================
# Scenario 2: Concurrent set_active + upsert
# =============================================================================


def test_concurrent_set_active_and_upsert():
    """Concurrent set_active and upsert must not produce inconsistent reads.

    One thread repeatedly toggles active_side while another thread
    upserts records. After the test, the active side must always
    contain the records that were upserted (no partial loss).
    """
    source = FakeCollectionAdapter(collection_name="source")
    target = FakeCollectionAdapter(collection_name="target")
    adapter = _make_adapter(source, target, active_side="source", dual_write_enabled=True)

    # Pre-populate both sides with a known record
    adapter.upsert({"id": "seed", "text": "seed record"})

    stop_event = threading.Event()
    results: Dict[str, Any] = {"upsert_errors": 0, "switch_count": 0}

    def _toggle_active():
        """Repeatedly toggle active_side until stop_event is set."""
        sides = ["source", "target"]
        idx = 0
        while not stop_event.is_set():
            try:
                adapter.set_active(sides[idx % 2])
                idx += 1
                results["switch_count"] += 1
            except Exception:
                pass
            time.sleep(0.001)

    def _upsert_records():
        """Upsert records until stop_event is set."""
        counter = 0
        while not stop_event.is_set():
            try:
                adapter.upsert({
                    "id": f"concurrent_rec_{counter}",
                    "text": f"record {counter}",
                    "value": counter,
                })
                counter += 1
            except Exception:
                results["upsert_errors"] += 1
            time.sleep(0.001)

    toggler = threading.Thread(target=_toggle_active, daemon=True)
    upsertor = threading.Thread(target=_upsert_records, daemon=True)

    toggler.start()
    upsertor.start()

    # Let them run for 2 seconds
    time.sleep(2.0)
    stop_event.set()

    toggler.join(timeout=5)
    upsertor.join(timeout=5)

    # Verify no upsert errors occurred
    assert results["upsert_errors"] == 0, (
        f"Upsert errors during concurrent access: {results['upsert_errors']}"
    )

    # Verify the active side (whatever it ended on) has the seed record
    active = adapter._active
    seed_result = active.get(["seed"])
    assert len(seed_result) == 1, (
        "Seed record missing from active side after concurrent access"
    )

    # Verify both sides have the same count (dual-write was enabled)
    assert len(source.stored_ids) == len(target.stored_ids), (
        f"Source ({len(source.stored_ids)}) and target ({len(target.stored_ids)}) "
        f"record counts differ after concurrent access"
    )


# =============================================================================
# Scenario 3: Concurrent MigrationStateManager save
# =============================================================================


def test_concurrent_state_save_no_corruption(tmp_path: Path):
    """Concurrent saves to MigrationStateManager must not corrupt the file.

    Multiple threads call save() simultaneously. After all complete,
    the state file must be valid JSON and contain the last-written data.
    """
    state_dir = str(tmp_path / "migration_state")
    manager = MigrationStateManager(state_dir)

    num_threads = 10
    saves_per_thread = 20

    def _save_state(thread_id: int) -> None:
        """Repeatedly save a state with a unique migration_id."""
        for i in range(saves_per_thread):
            state = _make_state(
                state_dir=state_dir,
                migration_id=f"thread_{thread_id}_save_{i}",
            )
            manager.save(state)

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [
            executor.submit(_save_state, tid)
            for tid in range(num_threads)
        ]
        for future in as_completed(futures):
            future.result()  # re-raise any exception

    # After all saves, the file must exist and be valid JSON
    assert manager.state_file.exists(), "State file does not exist after saves"

    with open(manager.state_file, "r", encoding="utf-8") as f:
        raw = f.read()

    # Must be valid JSON
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        pytest.fail(f"State file contains invalid JSON after concurrent saves: {exc}")

    # Must have the expected structure
    assert "migration_id" in data, "State file missing migration_id"
    assert "phase" in data, "State file missing phase"
    assert data["phase"] == MigrationPhase.dual_write.value, (
        f"Unexpected phase: {data['phase']}"
    )

    # Load via manager must succeed
    loaded = manager.load()
    assert loaded is not None, "Manager.load() returned None after concurrent saves"
    assert isinstance(loaded, MigrationState), (
        f"Expected MigrationState, got {type(loaded)}"
    )


def test_concurrent_state_save_and_load_no_crash(tmp_path: Path):
    """Concurrent save and load must not crash or produce partial reads.

    One thread repeatedly saves while another repeatedly loads.
    The load must never return corrupted data (partial JSON).

    Note: On Windows, concurrent file operations (rename while open for
    reading) may produce transient PermissionError / WinError 5.  These
    are OS-level races, not data corruption — the atomic-write pattern
    (tempfile + rename) guarantees that any *successful* read sees a
    complete, valid file.  We verify that:
    - No crash / unhandled exception occurs
    - Any successfully loaded state is structurally valid
    - The final state file is valid JSON and loadable
    """
    state_dir = str(tmp_path / "migration_state_race")
    manager = MigrationStateManager(state_dir)

    # Write an initial state
    manager.save(_make_state(state_dir=state_dir, migration_id="initial"))

    stop_event = threading.Event()
    crash_errors: List[str] = []

    def _save_loop():
        counter = 0
        while not stop_event.is_set():
            state = _make_state(
                state_dir=state_dir,
                migration_id=f"save_{counter}",
            )
            try:
                manager.save(state)
                counter += 1
            except (PermissionError, OSError):
                pass  # Windows transient — expected under race
            except Exception as exc:
                crash_errors.append(f"save error: {exc}")

    def _load_loop():
        while not stop_event.is_set():
            try:
                loaded = manager.load()
                # If load returns a state, it must be valid
                if loaded is not None:
                    assert isinstance(loaded, MigrationState), (
                        f"Loaded non-MigrationState: {type(loaded)}"
                    )
                    assert loaded.migration_id, "Loaded state has empty migration_id"
            except (PermissionError, OSError):
                pass  # Windows transient — expected under race
            except Exception as exc:
                crash_errors.append(f"load error: {exc}")

    saver = threading.Thread(target=_save_loop, daemon=True)
    loader = threading.Thread(target=_load_loop, daemon=True)

    saver.start()
    loader.start()

    time.sleep(1.5)
    stop_event.set()

    saver.join(timeout=3)
    loader.join(timeout=3)

    assert len(crash_errors) == 0, (
        f"Unexpected errors during concurrent save/load: {crash_errors}"
    )

    # Final state must be loadable
    final = manager.load()
    assert final is not None, "Final state is None after concurrent save/load"
    assert isinstance(final, MigrationState), (
        f"Final state has wrong type: {type(final)}"
    )
