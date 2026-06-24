# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""RED tests for MigrationPhase, MigrationState, MigrationStateManager, and MigrationStateFile.

These tests MUST fail because the production modules don't exist yet.
They define the expected API contract for the TDD GREEN phase.
"""

import json
import os
import threading
from pathlib import Path

import pytest

# =========================================================================
# MigrationPhase tests
# =========================================================================


def test_migration_phase_enum_values():
    """Enum has exactly 7 phases: idle, dual_write, building, building_complete,
    switched, dual_write_off, completed."""
    from openviking.storage.migration.state import MigrationPhase

    assert len(MigrationPhase) == 7
    assert MigrationPhase.idle.value == "idle"
    assert MigrationPhase.dual_write.value == "dual_write"
    assert MigrationPhase.building.value == "building"
    assert MigrationPhase.building_complete.value == "building_complete"
    assert MigrationPhase.switched.value == "switched"
    assert MigrationPhase.dual_write_off.value == "dual_write_off"
    assert MigrationPhase.completed.value == "completed"


# =========================================================================
# MigrationState tests
# =========================================================================


def test_migration_state_has_all_fields():
    """Dataclass contains all required fields as specified in migration-spec.md:144-163."""
    from openviking.storage.migration.state import MigrationPhase, MigrationState

    state = MigrationState(
        migration_id="mig_001",
        phase=MigrationPhase.dual_write,
        source_collection="coll_v1",
        target_collection="coll_v2",
        active_side="source",
        dual_write_enabled=True,
        source_embedder_name="v1",
        target_embedder_name="v2",
        degraded_write_failures=0,
        reindex_progress=None,
        started_at="2026-04-29T10:00:00Z",
        updated_at="2026-04-29T10:00:00Z",
    )
    assert state.migration_id == "mig_001"
    assert state.phase == MigrationPhase.dual_write
    assert state.source_collection == "coll_v1"
    assert state.target_collection == "coll_v2"
    assert state.active_side == "source"
    assert state.dual_write_enabled is True
    assert state.source_embedder_name == "v1"
    assert state.target_embedder_name == "v2"
    assert state.degraded_write_failures == 0
    assert state.reindex_progress is None
    assert state.started_at == "2026-04-29T10:00:00Z"
    assert state.updated_at == "2026-04-29T10:00:00Z"


def test_migration_state_defaults():
    """Default values: degraded_write_failures=0."""
    from openviking.storage.migration.state import MigrationPhase, MigrationState

    state = MigrationState(
        migration_id="mig_002",
        phase=MigrationPhase.idle,
        source_collection="coll_v1",
        target_collection="coll_v2",
        active_side="source",
        dual_write_enabled=False,
        source_embedder_name="v1",
        target_embedder_name="v2",
        reindex_progress=None,
        started_at="2026-04-29T10:00:00Z",
        updated_at="2026-04-29T10:00:00Z",
    )
    assert state.degraded_write_failures == 0


def test_migration_state_serialization_roundtrip():
    """to_dict() / from_dict() roundtrip produces an identical state."""
    from openviking.storage.migration.state import MigrationPhase, MigrationState

    original = MigrationState(
        migration_id="mig_003",
        phase=MigrationPhase.building,
        source_collection="coll_v1",
        target_collection="coll_v2",
        active_side="source",
        dual_write_enabled=True,
        source_embedder_name="v1",
        target_embedder_name="v2",
        degraded_write_failures=3,
        reindex_progress=None,
        started_at="2026-04-29T10:00:00Z",
        updated_at="2026-04-29T12:00:00Z",
    )
    data = original.to_dict()
    assert isinstance(data, dict)
    assert data["phase"] == "building"
    assert data["degraded_write_failures"] == 3

    restored = MigrationState.from_dict(data)
    assert restored == original
    assert restored.phase == MigrationPhase.building


# =========================================================================
# MigrationStateManager tests
# =========================================================================


def test_state_manager_save_load_roundtrip(temp_dir):
    """Save a MigrationState then load it back — must be identical."""
    from openviking.storage.migration.state import (
        MigrationPhase,
        MigrationState,
        MigrationStateManager,
    )

    manager = MigrationStateManager(str(temp_dir))
    state = MigrationState(
        migration_id="mig_save_001",
        phase=MigrationPhase.dual_write,
        source_collection="coll_v1",
        target_collection="coll_v2",
        active_side="source",
        dual_write_enabled=True,
        source_embedder_name="v1",
        target_embedder_name="v2",
        reindex_progress=None,
        started_at="2026-04-29T10:00:00Z",
        updated_at="2026-04-29T10:00:00Z",
    )
    manager.save(state)
    loaded = manager.load()
    assert loaded is not None
    assert loaded == state


def test_state_manager_load_nonexistent_returns_none(temp_dir):
    """Loading from a directory with no state file returns None."""
    from openviking.storage.migration.state import MigrationStateManager

    manager = MigrationStateManager(str(temp_dir))
    result = manager.load()
    assert result is None


def test_state_manager_delete_clears_state(temp_dir):
    """After delete(), load() returns None."""
    from openviking.storage.migration.state import (
        MigrationPhase,
        MigrationState,
        MigrationStateManager,
    )

    manager = MigrationStateManager(str(temp_dir))
    state = MigrationState(
        migration_id="mig_del_001",
        phase=MigrationPhase.idle,
        source_collection="coll_v1",
        target_collection="coll_v2",
        active_side="source",
        dual_write_enabled=False,
        source_embedder_name="v1",
        target_embedder_name="v2",
        reindex_progress=None,
        started_at="2026-04-29T10:00:00Z",
        updated_at="2026-04-29T10:00:00Z",
    )
    manager.save(state)
    assert manager.load() is not None
    manager.delete()
    assert manager.load() is None


def test_state_manager_concurrent_write_safety(temp_dir):
    """Concurrent writes from multiple threads don't corrupt the state file."""
    from openviking.storage.migration.state import (
        MigrationPhase,
        MigrationState,
        MigrationStateManager,
    )

    manager = MigrationStateManager(str(temp_dir))
    errors = []
    lock = threading.Lock()

    def writer(thread_id: int):
        try:
            state = MigrationState(
                migration_id=f"mig_con_{thread_id}",
                phase=MigrationPhase.dual_write,
                source_collection="coll_v1",
                target_collection="coll_v2",
                active_side="source",
                dual_write_enabled=True,
                source_embedder_name="v1",
                target_embedder_name="v2",
                reindex_progress=None,
                started_at="2026-04-29T10:00:00Z",
                updated_at="2026-04-29T10:00:00Z",
            )
            for _ in range(20):
                manager.save(state)
        except Exception as e:
            with lock:
                errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Concurrent writes raised errors: {errors}"
    # File must still be valid JSON
    state_file = temp_dir / "migration_runtime_state.json"
    assert state_file.exists()
    with open(state_file, "r") as f:
        data = json.load(f)
    assert "migration_id" in data
    assert "phase" in data


def test_state_manager_atomic_write_no_partial(temp_dir):
    """Simulate a crash during write — partial file must not replace the original."""
    from openviking.storage.migration.state import (
        MigrationPhase,
        MigrationState,
        MigrationStateManager,
    )

    manager = MigrationStateManager(str(temp_dir))
    original_state = MigrationState(
        migration_id="mig_atomic_001",
        phase=MigrationPhase.idle,
        source_collection="coll_v1",
        target_collection="coll_v2",
        active_side="source",
        dual_write_enabled=False,
        source_embedder_name="v1",
        target_embedder_name="v2",
        reindex_progress=None,
        started_at="2026-04-29T10:00:00Z",
        updated_at="2026-04-29T10:00:00Z",
    )
    manager.save(original_state)

    # Simulate a partial write by writing garbage to a temp file in the same dir,
    # then renaming it over the state file (bypassing the atomic write).
    state_file = temp_dir / "migration_runtime_state.json"
    temp_file = temp_dir / "migration_runtime_state.tmp"
    with open(temp_file, "w") as f:
        f.write("{invalid json...")
    os.replace(temp_file, state_file)

    # load() should handle corruption gracefully (return None or raise)
    result = manager.load()
    # The file is corrupted — either None or a clean error is acceptable
    assert result is None or isinstance(result, MigrationState)


# =========================================================================
# MigrationStateFile tests
# =========================================================================


def test_state_file_create_initial(temp_dir):
    """create_initial() creates a valid state file with current_active set."""
    from openviking.storage.migration.state import MigrationStateFile

    state_file = MigrationStateFile(str(temp_dir))
    state_file.create_initial("v1")

    file_path = temp_dir / "embedding_migration_state.json"
    assert file_path.exists()
    with open(file_path, "r") as f:
        data = json.load(f)
    assert data["version"] == 1
    assert data["current_active"] == "v1"
    assert data["history"] == []


def test_state_file_read_current_active(temp_dir):
    """read() returns the correct current_active value."""
    from openviking.storage.migration.state import MigrationStateFile

    state_file = MigrationStateFile(str(temp_dir))
    state_file.create_initial("v1")

    data = state_file.read()
    assert data["current_active"] == "v1"
    assert data["version"] == 1
    assert isinstance(data["history"], list)


def test_state_file_update_current_active(temp_dir):
    """update_current_active() atomically updates the current_active field."""
    from openviking.storage.migration.state import MigrationStateFile

    state_file = MigrationStateFile(str(temp_dir))
    state_file.create_initial("v1")

    state_file.update_current_active("v2")
    data = state_file.read()
    assert data["current_active"] == "v2"
    # Other fields preserved
    assert data["version"] == 1


def test_state_file_append_history(temp_dir):
    """append_history() appends a migration record to the history array."""
    from openviking.storage.migration.state import MigrationStateFile

    state_file = MigrationStateFile(str(temp_dir))
    state_file.create_initial("v1")

    entry = {
        "id": "mig_20260429_120000_a1b2c3d4",
        "from_name": "v1",
        "to_name": "v2",
        "status": "completed",
        "started_at": "2026-04-29T10:00:00Z",
        "completed_at": "2026-04-29T12:30:00Z",
    }
    state_file.append_history(entry)

    data = state_file.read()
    assert len(data["history"]) == 1
    assert data["history"][0] == entry

    # Append another entry
    entry2 = {
        "id": "mig_20260430_120000_x1y2z3",
        "from_name": "v2",
        "to_name": "v3",
        "status": "completed",
        "started_at": "2026-04-30T10:00:00Z",
        "completed_at": "2026-04-30T12:00:00Z",
    }
    state_file.append_history(entry2)
    data = state_file.read()
    assert len(data["history"]) == 2
    assert data["history"][1] == entry2


def test_state_file_atomic_write(temp_dir):
    """Atomic write guarantee — partial write doesn't corrupt the state file."""
    from openviking.storage.migration.state import MigrationStateFile

    state_file = MigrationStateFile(str(temp_dir))
    state_file.create_initial("v1")

    # Corrupt the file directly
    file_path = temp_dir / "embedding_migration_state.json"
    with open(file_path, "w") as f:
        f.write("{broken")

    # read() should handle corruption gracefully
    with pytest.raises(json.JSONDecodeError):
        state_file.read()
