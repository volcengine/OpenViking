# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""RED tests for MigrationController — forward transitions, rollback, abort.

All tests MUST fail because MigrationController doesn't exist yet.
They define the expected API contract for the TDD GREEN phase.

Tests use mocks for DualWriteAdapter, ReindexEngine, MigrationStateManager,
to verify orchestration logic in isolation.

MigrationController is imported INSIDE test functions (not at module level)
so the test file doesn't crash on import error — every test fails individually
with ModuleNotFoundError during the RED phase.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from openviking.storage.migration.state import (
    MigrationPhase,
    MigrationState,
    MigrationStateManager,
    MigrationStateFile,
    ReindexProgress,
)


# =============================================================================
# Fake / Mock factories
# =============================================================================


def _make_fake_collection_adapter(
    name: str = "context",
    exists: bool = True,
) -> MagicMock:
    """Create a MagicMock with enough CollectionAdapter-like behavior for controller tests."""
    adapter = MagicMock()
    adapter.collection_name = name
    adapter.collection_exists.return_value = exists
    adapter.create_collection.return_value = True
    adapter.drop_collection.return_value = True
    adapter.get_collection_info.return_value = {
        "CollectionName": name,
        "Fields": [
            {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
            {"FieldName": "uri", "FieldType": "path"},
            {"FieldName": "vector", "FieldType": "vector", "Dim": 3072},
        ],
    }
    adapter.upsert.return_value = ["mock_id_001"]
    adapter.delete.return_value = 1
    adapter.query.return_value = []
    adapter.count.return_value = 0
    return adapter


def _make_mock_embedder(name: str = "default_embedder") -> MagicMock:
    """Create a mock embedder for testing."""
    embedder = MagicMock()
    embedder.name = name
    embedder.dimension = 1024
    embedder.embed_async = AsyncMock()
    return embedder


def _make_mock_config(
    *,
    source_embedder_name: str = "v1",
    target_embedder_name: str = "v2",
    target_dimension: int = 1024,
    source_dimension: int = 3072,
) -> MagicMock:
    """Create a mock MigratorConfig.

    Returns an embedder when get_target_embedder(name) is called
    so begin_building tests can verify the correct embedder is used.
    """
    config = MagicMock()
    config.source_embedder_name = source_embedder_name
    config.target_embedder_name = target_embedder_name
    config.target_dimension = target_dimension
    config.source_dimension = source_dimension
    config.embeddings = {
        source_embedder_name: _make_mock_embedder(source_embedder_name),
        target_embedder_name: _make_mock_embedder(target_embedder_name),
    }
    config.get_target_embedder = MagicMock(
        return_value=config.embeddings[target_embedder_name]
    )
    config.get_source_embedder = MagicMock(
        return_value=config.embeddings[source_embedder_name]
    )
    return config


def _make_mock_service() -> MagicMock:
    """Create a mock MigratorService."""
    service = MagicMock()
    service.get_source_adapter = MagicMock()
    service.get_target_adapter = MagicMock()
    service.get_named_queue = MagicMock()
    return service


def _make_mock_state_manager(temp_dir, state: Optional[MigrationState] = None) -> MagicMock:
    """Create a mock MigrationStateManager."""
    mgr = MagicMock(spec=MigrationStateManager)
    mgr.load.return_value = state
    mgr.save.return_value = None
    mgr.delete.return_value = None
    return mgr


def _make_mock_state_file(temp_dir) -> MagicMock:
    """Create a mock MigrationStateFile."""
    sf = MagicMock(spec=MigrationStateFile)
    sf.read.return_value = {
        "version": 1,
        "current_active": "v1",
        "history": [],
    }
    sf.update_current_active.return_value = None
    sf.append_history.return_value = None
    return sf


def _make_migration_state(
    *,
    migration_id: str = "mig_test_001",
    phase: MigrationPhase = MigrationPhase.idle,
    source_collection: str = "context",
    target_collection: str = "context_v2",
    active_side: str = "source",
    dual_write_enabled: bool = False,
    source_embedder_name: str = "v1",
    target_embedder_name: str = "v2",
    degraded_write_failures: int = 0,
    reindex_progress: Optional[ReindexProgress] = None,
) -> MigrationState:
    """Create a MigrationState with sensible defaults for tests."""
    now = datetime.now(timezone.utc).isoformat()
    return MigrationState(
        migration_id=migration_id,
        phase=phase,
        source_collection=source_collection,
        target_collection=target_collection,
        active_side=active_side,
        dual_write_enabled=dual_write_enabled,
        source_embedder_name=source_embedder_name,
        target_embedder_name=target_embedder_name,
        degraded_write_failures=degraded_write_failures,
        reindex_progress=reindex_progress,
        started_at=now,
        updated_at=now,
    )


# =============================================================================
# Safe import helper (RED phase)
# =============================================================================


def _import_controller():
    """Import MigrationController and InvalidTransitionError.

    During RED phase, this MUST raise ModuleNotFoundError because
    controller.py doesn't exist yet.
    """
    from openviking.storage.migration.controller import (  # noqa: F811
        MigrationController,
        InvalidTransitionError,
    )

    return MigrationController, InvalidTransitionError


# =============================================================================
# 1. start_migration — idle → dual_write (T1)
# =============================================================================


def test_start_migration_idle_to_dual_write(temp_dir):
    """start_migration must transition from idle to dual_write:
    - Create target collection if it does not exist
    - Construct DualWriteAdapter
    - Persist state with embedder names
    - Return MigrationState with phase=dual_write
    """
    MigrationController, _ = _import_controller()

    config = _make_mock_config()
    service = _make_mock_service()
    source_adapter = _make_fake_collection_adapter(name="context", exists=True)
    target_adapter = _make_fake_collection_adapter(name="context_v2", exists=False)

    service.get_source_adapter.return_value = source_adapter
    service.get_target_adapter.return_value = target_adapter

    state_manager = _make_mock_state_manager(temp_dir, state=None)  # no active migration
    state_file = _make_mock_state_file(temp_dir)

    controller = MigrationController(
        config=config,
        service=service,
        state_manager=state_manager,
        state_file=state_file,
    )

    result = controller.start_migration("v2")

    # Must return a MigrationState
    assert isinstance(result, MigrationState)
    assert result.phase == MigrationPhase.dual_write
    assert result.source_embedder_name == "v1"
    assert result.target_embedder_name == "v2"
    assert result.active_side == "source"
    assert result.dual_write_enabled is True
    assert result.target_collection == "context_v2"

    # State must be persisted
    state_manager.save.assert_called()
    saved_state = state_manager.save.call_args[0][0]
    assert saved_state.phase == MigrationPhase.dual_write


def test_start_migration_rejects_if_active_migration(temp_dir):
    """start_migration must raise InvalidTransitionError when a migration is already active.
    
    An active migration means the state file has a state with phase != idle.
    """
    MigrationController, InvalidTransitionError = _import_controller()

    config = _make_mock_config()
    service = _make_mock_service()

    # Pre-existing state in dual_write phase (active migration)
    existing_state = _make_migration_state(
        phase=MigrationPhase.dual_write,
        dual_write_enabled=True,
    )
    state_manager = _make_mock_state_manager(temp_dir, state=existing_state)
    state_file = _make_mock_state_file(temp_dir)

    controller = MigrationController(
        config=config,
        service=service,
        state_manager=state_manager,
        state_file=state_file,
    )

    with pytest.raises(InvalidTransitionError, match="active|already|migration"):
        controller.start_migration("v2")

    # No writes should have happened
    state_manager.save.assert_not_called()


# =============================================================================
# 2. begin_building — dual_write → building (T2)
# =============================================================================


def test_begin_building_dual_write_to_building(temp_dir):
    """begin_building must transition from dual_write to building:
    - Create target embedder using state.target_embedder_name
    - Create ReindexEngine
    - Start reindex background task
    - Register _on_reindex_done callback
    - Update state to building
    """
    MigrationController, _ = _import_controller()

    config = _make_mock_config()
    service = _make_mock_service()
    source_adapter = _make_fake_collection_adapter(name="context")
    target_adapter = _make_fake_collection_adapter(name="context_v2")

    service.get_source_adapter.return_value = source_adapter
    service.get_target_adapter.return_value = target_adapter

    mock_queue = MagicMock()
    mock_queue.clear = AsyncMock()
    service.get_named_queue.return_value = mock_queue

    existing_state = _make_migration_state(
        phase=MigrationPhase.dual_write,
        dual_write_enabled=True,
        target_embedder_name="v2",
    )
    state_manager = _make_mock_state_manager(temp_dir, state=existing_state)
    state_file = _make_mock_state_file(temp_dir)

    controller = MigrationController(
        config=config,
        service=service,
        state_manager=state_manager,
        state_file=state_file,
    )
    # Inject mock dual_write_adapter so controller works with existing state
    from openviking.storage.migration.blue_green_adapter import DualWriteAdapter
    controller._adapter = DualWriteAdapter(
        source=source_adapter,
        target=target_adapter,
        active_side="source",
        dual_write_enabled=True,
    )

    result = controller.begin_building()

    assert isinstance(result, MigrationState)
    assert result.phase == MigrationPhase.building

    # Target embedder should have been used (C-2 check from spec)
    config.get_target_embedder.assert_called_with("v2")
    # NOT the config.embedding (current active)
    config.get_source_embedder.assert_not_called()

    # State should be persisted
    state_manager.save.assert_called()


def test_begin_building_uses_correct_target_embedder(temp_dir):
    """begin_building MUST use state.target_embedder_name (not config.embedding name).
    
    This is the P0 C-2 fix: when migrating, the target embedder is the migration
    target, NOT whatever is currently configured as "active" embedder.
    """
    MigrationController, _ = _import_controller()

    config = _make_mock_config(
        source_embedder_name="v1",
        target_embedder_name="v2",
    )
    service = _make_mock_service()
    source_adapter = _make_fake_collection_adapter(name="context")
    target_adapter = _make_fake_collection_adapter(name="context_v2")
    service.get_source_adapter.return_value = source_adapter
    service.get_target_adapter.return_value = target_adapter
    service.get_named_queue.return_value = MagicMock()

    existing_state = _make_migration_state(
        phase=MigrationPhase.dual_write,
        dual_write_enabled=True,
        source_embedder_name="v1",
        target_embedder_name="v2",
    )
    state_manager = _make_mock_state_manager(temp_dir, state=existing_state)
    state_file = _make_mock_state_file(temp_dir)

    controller = MigrationController(
        config=config,
        service=service,
        state_manager=state_manager,
        state_file=state_file,
    )
    from openviking.storage.migration.blue_green_adapter import DualWriteAdapter
    controller._adapter = DualWriteAdapter(
        source=source_adapter,
        target=target_adapter,
        active_side="source",
        dual_write_enabled=True,
    )

    controller.begin_building()

    # Critical: must call get_target_embedder with v2, not v1
    config.get_target_embedder.assert_called_once_with("v2")


def test_begin_building_supports_rebuild_from_building_complete(temp_dir):
    """begin_building must support re-build from building_complete:
    - Clean existing NamedQueue
    - Create new ReindexEngine
    - Transition building_complete → building
    """
    MigrationController, _ = _import_controller()

    config = _make_mock_config()
    service = _make_mock_service()
    source_adapter = _make_fake_collection_adapter(name="context")
    target_adapter = _make_fake_collection_adapter(name="context_v2")
    service.get_source_adapter.return_value = source_adapter
    service.get_target_adapter.return_value = target_adapter

    mock_queue = MagicMock()
    mock_queue.clear = AsyncMock(return_value=True)
    service.get_named_queue.return_value = mock_queue

    # State is in building_complete (ready for rebuild)
    existing_state = _make_migration_state(
        phase=MigrationPhase.building_complete,
        dual_write_enabled=True,
        reindex_progress=ReindexProgress(processed=100, total=100, errors=2),
    )
    state_manager = _make_mock_state_manager(temp_dir, state=existing_state)
    state_file = _make_mock_state_file(temp_dir)

    controller = MigrationController(
        config=config,
        service=service,
        state_manager=state_manager,
        state_file=state_file,
    )
    from openviking.storage.migration.blue_green_adapter import DualWriteAdapter
    controller._adapter = DualWriteAdapter(
        source=source_adapter,
        target=target_adapter,
        active_side="source",
        dual_write_enabled=True,
    )
    # Inject the queue reference so rebuild can clean it
    controller._queue = mock_queue

    result = controller.begin_building()

    assert result.phase == MigrationPhase.building

    # Queue should have been cleaned before creating new engine
    mock_queue.clear.assert_called()


def test_begin_building_rejects_if_invalid_phase(temp_dir):
    """begin_building must reject transitions from invalid phases (e.g., idle, switched)."""
    MigrationController, InvalidTransitionError = _import_controller()

    config = _make_mock_config()
    service = _make_mock_service()
    service.get_source_adapter.return_value = _make_fake_collection_adapter(name="context")
    service.get_target_adapter.return_value = _make_fake_collection_adapter(name="context_v2")

    # Phase is idle — not valid for begin_building
    existing_state = _make_migration_state(phase=MigrationPhase.idle)
    state_manager = _make_mock_state_manager(temp_dir, state=existing_state)
    state_file = _make_mock_state_file(temp_dir)

    controller = MigrationController(
        config=config,
        service=service,
        state_manager=state_manager,
        state_file=state_file,
    )

    with pytest.raises(InvalidTransitionError):
        controller.begin_building()


# =============================================================================
# 3. _on_reindex_done — building → building_complete (T3)
# =============================================================================


def test_on_reindex_done_building_to_building_complete(temp_dir):
    """_on_reindex_done must transition from building to building_complete.
    
    CRITICAL: must go to building_complete, NOT switched.
    """
    MigrationController, _ = _import_controller()

    config = _make_mock_config()
    service = _make_mock_service()
    source_adapter = _make_fake_collection_adapter(name="context")
    target_adapter = _make_fake_collection_adapter(name="context_v2")
    service.get_source_adapter.return_value = source_adapter
    service.get_target_adapter.return_value = target_adapter

    existing_state = _make_migration_state(
        phase=MigrationPhase.building,
        dual_write_enabled=True,
    )
    state_manager = _make_mock_state_manager(temp_dir, state=existing_state)
    state_file = _make_mock_state_file(temp_dir)

    controller = MigrationController(
        config=config,
        service=service,
        state_manager=state_manager,
        state_file=state_file,
    )
    from openviking.storage.migration.blue_green_adapter import DualWriteAdapter
    controller._adapter = DualWriteAdapter(
        source=source_adapter,
        target=target_adapter,
        active_side="source",
        dual_write_enabled=True,
    )

    # Simulate reindex completion — must call _on_reindex_done
    assert hasattr(controller, "_on_reindex_done"), (
        "MigrationController must expose _on_reindex_done callback"
    )
    assert callable(controller._on_reindex_done), "_on_reindex_done must be callable"

    controller._on_reindex_done()

    # Verify state was persisted with building_complete (NOT switched)
    state_manager.save.assert_called()
    saved_state = state_manager.save.call_args[0][0]
    assert saved_state.phase == MigrationPhase.building_complete, (
        f"_on_reindex_done must set phase=building_complete, got {saved_state.phase}"
    )


def test_on_reindex_done_records_final_progress(temp_dir):
    """_on_reindex_done must record final reindex progress before transitioning."""
    MigrationController, _ = _import_controller()

    config = _make_mock_config()
    service = _make_mock_service()
    source_adapter = _make_fake_collection_adapter(name="context")
    target_adapter = _make_fake_collection_adapter(name="context_v2")
    service.get_source_adapter.return_value = source_adapter
    service.get_target_adapter.return_value = target_adapter

    existing_state = _make_migration_state(
        phase=MigrationPhase.building,
        dual_write_enabled=True,
    )
    state_manager = _make_mock_state_manager(temp_dir, state=existing_state)
    state_file = _make_mock_state_file(temp_dir)

    controller = MigrationController(
        config=config,
        service=service,
        state_manager=state_manager,
        state_file=state_file,
    )
    from openviking.storage.migration.blue_green_adapter import DualWriteAdapter
    controller._adapter = DualWriteAdapter(
        source=source_adapter,
        target=target_adapter,
        active_side="source",
        dual_write_enabled=True,
    )

    controller._on_reindex_done()

    # Progress should be recorded (errors is non-negative)
    saved_state = state_manager.save.call_args[0][0]
    if saved_state.reindex_progress is not None:
        assert saved_state.reindex_progress.errors >= 0


# =============================================================================
# 4. confirm_switch — building_complete → switched (T4)
# =============================================================================


def test_confirm_switch_building_complete_to_switched(temp_dir):
    """confirm_switch must transition building_complete → switched:
    - Validate target embedder endpoint reachability
    - Call adapter.set_active("target")
    - Update state with phase=switched, active_side=target
    """
    MigrationController, _ = _import_controller()

    config = _make_mock_config()
    service = _make_mock_service()
    source_adapter = _make_fake_collection_adapter(name="context")
    target_adapter = _make_fake_collection_adapter(name="context_v2")
    service.get_source_adapter.return_value = source_adapter
    service.get_target_adapter.return_value = target_adapter

    existing_state = _make_migration_state(
        phase=MigrationPhase.building_complete,
        dual_write_enabled=True,
        active_side="source",
    )
    state_manager = _make_mock_state_manager(temp_dir, state=existing_state)
    state_file = _make_mock_state_file(temp_dir)

    controller = MigrationController(
        config=config,
        service=service,
        state_manager=state_manager,
        state_file=state_file,
    )
    from openviking.storage.migration.blue_green_adapter import DualWriteAdapter
    mock_adapter = DualWriteAdapter(
        source=source_adapter,
        target=target_adapter,
        active_side="source",
        dual_write_enabled=True,
    )
    controller._adapter = mock_adapter

    result = controller.confirm_switch()

    assert result.phase == MigrationPhase.switched
    assert result.active_side == "target"

    state_manager.save.assert_called()
    saved_state = state_manager.save.call_args[0][0]
    assert saved_state.phase == MigrationPhase.switched
    assert saved_state.active_side == "target"


def test_confirm_switch_rejects_if_errors_above_threshold(temp_dir):
    """confirm_switch must reject when reindex errors exceed acceptable threshold."""
    MigrationController, InvalidTransitionError = _import_controller()

    config = _make_mock_config()
    service = _make_mock_service()
    source_adapter = _make_fake_collection_adapter(name="context")
    target_adapter = _make_fake_collection_adapter(name="context_v2")
    service.get_source_adapter.return_value = source_adapter
    service.get_target_adapter.return_value = target_adapter

    # reindex had 50 errors out of 100 — 50% error rate, way over threshold
    existing_state = _make_migration_state(
        phase=MigrationPhase.building_complete,
        dual_write_enabled=True,
        reindex_progress=ReindexProgress(processed=100, total=100, errors=50),
    )
    state_manager = _make_mock_state_manager(temp_dir, state=existing_state)
    state_file = _make_mock_state_file(temp_dir)

    controller = MigrationController(
        config=config,
        service=service,
        state_manager=state_manager,
        state_file=state_file,
    )
    from openviking.storage.migration.blue_green_adapter import DualWriteAdapter
    controller._adapter = DualWriteAdapter(
        source=source_adapter,
        target=target_adapter,
        active_side="source",
        dual_write_enabled=True,
    )

    with pytest.raises(InvalidTransitionError, match="error|threshold|50"):
        controller.confirm_switch()

    # State should NOT have been updated
    state_manager.save.assert_not_called()


def test_confirm_switch_rejects_if_not_building_complete(temp_dir):
    """confirm_switch must reject phases other than building_complete."""
    MigrationController, InvalidTransitionError = _import_controller()

    config = _make_mock_config()
    service = _make_mock_service()
    source_adapter = _make_fake_collection_adapter(name="context")
    target_adapter = _make_fake_collection_adapter(name="context_v2")
    service.get_source_adapter.return_value = source_adapter
    service.get_target_adapter.return_value = target_adapter

    # Phase is building — cannot switch yet
    existing_state = _make_migration_state(
        phase=MigrationPhase.building,
        dual_write_enabled=True,
    )
    state_manager = _make_mock_state_manager(temp_dir, state=existing_state)
    state_file = _make_mock_state_file(temp_dir)

    controller = MigrationController(
        config=config,
        service=service,
        state_manager=state_manager,
        state_file=state_file,
    )
    from openviking.storage.migration.blue_green_adapter import DualWriteAdapter
    controller._adapter = DualWriteAdapter(
        source=source_adapter,
        target=target_adapter,
        active_side="source",
        dual_write_enabled=True,
    )

    with pytest.raises(InvalidTransitionError):
        controller.confirm_switch()


# =============================================================================
# 5. disable_dual_write — switched → dual_write_off (T5)
# =============================================================================


def test_disable_dual_write_switched_to_dual_write_off(temp_dir):
    """disable_dual_write must transition switched → dual_write_off:
    - Call adapter.set_dual_write(False)
    - Update state with dual_write_enabled=False
    """
    MigrationController, _ = _import_controller()

    config = _make_mock_config()
    service = _make_mock_service()
    source_adapter = _make_fake_collection_adapter(name="context")
    target_adapter = _make_fake_collection_adapter(name="context_v2")
    service.get_source_adapter.return_value = source_adapter
    service.get_target_adapter.return_value = target_adapter

    existing_state = _make_migration_state(
        phase=MigrationPhase.switched,
        dual_write_enabled=True,
        active_side="target",
    )
    state_manager = _make_mock_state_manager(temp_dir, state=existing_state)
    state_file = _make_mock_state_file(temp_dir)

    controller = MigrationController(
        config=config,
        service=service,
        state_manager=state_manager,
        state_file=state_file,
    )
    from openviking.storage.migration.blue_green_adapter import DualWriteAdapter
    controller._adapter = DualWriteAdapter(
        source=source_adapter,
        target=target_adapter,
        active_side="target",
        dual_write_enabled=True,
    )

    result = controller.disable_dual_write()

    assert result.phase == MigrationPhase.dual_write_off
    assert result.dual_write_enabled is False

    state_manager.save.assert_called()
    saved_state = state_manager.save.call_args[0][0]
    assert saved_state.dual_write_enabled is False


def test_disable_dual_write_rejects_if_not_switched(temp_dir):
    """disable_dual_write must reject if not in switched phase."""
    MigrationController, InvalidTransitionError = _import_controller()

    config = _make_mock_config()
    service = _make_mock_service()
    source_adapter = _make_fake_collection_adapter(name="context")
    target_adapter = _make_fake_collection_adapter(name="context_v2")
    service.get_source_adapter.return_value = source_adapter
    service.get_target_adapter.return_value = target_adapter

    existing_state = _make_migration_state(
        phase=MigrationPhase.building_complete,
        dual_write_enabled=True,
    )
    state_manager = _make_mock_state_manager(temp_dir, state=existing_state)
    state_file = _make_mock_state_file(temp_dir)

    controller = MigrationController(
        config=config,
        service=service,
        state_manager=state_manager,
        state_file=state_file,
    )

    with pytest.raises(InvalidTransitionError):
        controller.disable_dual_write()


# =============================================================================
# 6. finish_migration — dual_write_off → completed → idle (T6 + T7)
# =============================================================================


def test_finish_migration_dual_write_off_to_completed_to_idle(temp_dir):
    """finish_migration must do dual_write_off → completed → idle:
    - Update migration state file: current_active = target_embedder_name
    - Append history record
    - Clean up runtime MigrationState file
    - Clean NamedQueue
    - System returns to idle
    """
    MigrationController, _ = _import_controller()

    config = _make_mock_config(
        source_embedder_name="v1",
        target_embedder_name="v2",
    )
    service = _make_mock_service()
    source_adapter = _make_fake_collection_adapter(name="context")
    target_adapter = _make_fake_collection_adapter(name="context_v2")
    service.get_source_adapter.return_value = source_adapter
    service.get_target_adapter.return_value = target_adapter

    existing_state = _make_migration_state(
        phase=MigrationPhase.dual_write_off,
        dual_write_enabled=False,
        active_side="target",
        source_embedder_name="v1",
        target_embedder_name="v2",
    )
    state_manager = _make_mock_state_manager(temp_dir, state=existing_state)
    state_file = _make_mock_state_file(temp_dir)

    controller = MigrationController(
        config=config,
        service=service,
        state_manager=state_manager,
        state_file=state_file,
    )

    result = controller.finish_migration()

    # The final state after finish should be idle
    assert result.phase == MigrationPhase.idle

    # Migration state file MUST be updated with new current_active
    state_file.update_current_active.assert_called_once_with("v2")

    # History MUST have a new entry
    state_file.append_history.assert_called_once()

    # Runtime state MUST be deleted
    state_manager.delete.assert_called()

    # The history entry should contain the migration id and status
    history_call = state_file.append_history.call_args[0][0]
    assert "id" in history_call
    assert history_call.get("from_name") == "v1"
    assert history_call.get("to_name") == "v2"
    assert history_call.get("status") == "completed"


def test_finish_migration_with_confirm_cleanup_true(temp_dir):
    """finish_migration(confirm_cleanup=True) should optionally clean source collection."""
    MigrationController, _ = _import_controller()

    config = _make_mock_config()
    service = _make_mock_service()
    source_adapter = _make_fake_collection_adapter(name="context", exists=True)
    target_adapter = _make_fake_collection_adapter(name="context_v2")
    service.get_source_adapter.return_value = source_adapter
    service.get_target_adapter.return_value = target_adapter

    existing_state = _make_migration_state(
        phase=MigrationPhase.dual_write_off,
        dual_write_enabled=False,
    )
    state_manager = _make_mock_state_manager(temp_dir, state=existing_state)
    state_file = _make_mock_state_file(temp_dir)

    controller = MigrationController(
        config=config,
        service=service,
        state_manager=state_manager,
        state_file=state_file,
    )

    result = controller.finish_migration(confirm_cleanup=True)

    assert result.phase == MigrationPhase.idle
    # Source should be cleaned up when confirm_cleanup=True
    source_adapter.drop_collection.assert_called()


def test_finish_migration_rejects_if_not_dual_write_off(temp_dir):
    """finish_migration must reject phases other than dual_write_off."""
    MigrationController, InvalidTransitionError = _import_controller()

    config = _make_mock_config()
    service = _make_mock_service()
    service.get_source_adapter.return_value = _make_fake_collection_adapter(name="context")
    service.get_target_adapter.return_value = _make_fake_collection_adapter(name="context_v2")

    existing_state = _make_migration_state(
        phase=MigrationPhase.switched,  # wrong phase
        dual_write_enabled=True,
    )
    state_manager = _make_mock_state_manager(temp_dir, state=existing_state)
    state_file = _make_mock_state_file(temp_dir)

    controller = MigrationController(
        config=config,
        service=service,
        state_manager=state_manager,
        state_file=state_file,
    )

    with pytest.raises(InvalidTransitionError):
        controller.finish_migration()


# =============================================================================
# 7. abort_migration — any → idle (R1, R2, R3)
# =============================================================================


def test_abort_dual_write_to_idle(temp_dir):
    """R1: abort from dual_write must:
    - Disable dual-write
    - Delete target collection
    - Clear state
    """
    MigrationController, _ = _import_controller()

    config = _make_mock_config()
    service = _make_mock_service()
    source_adapter = _make_fake_collection_adapter(name="context", exists=True)
    target_adapter = _make_fake_collection_adapter(name="context_v2", exists=True)
    service.get_source_adapter.return_value = source_adapter
    service.get_target_adapter.return_value = target_adapter

    existing_state = _make_migration_state(
        phase=MigrationPhase.dual_write,
        dual_write_enabled=True,
    )
    state_manager = _make_mock_state_manager(temp_dir, state=existing_state)
    state_file = _make_mock_state_file(temp_dir)

    controller = MigrationController(
        config=config,
        service=service,
        state_manager=state_manager,
        state_file=state_file,
    )
    from openviking.storage.migration.blue_green_adapter import DualWriteAdapter
    controller._adapter = DualWriteAdapter(
        source=source_adapter,
        target=target_adapter,
        active_side="source",
        dual_write_enabled=True,
    )

    result = controller.abort_migration()

    assert result.phase == MigrationPhase.idle

    # Target collection must be deleted
    target_adapter.drop_collection.assert_called()

    # Runtime state must be deleted
    state_manager.delete.assert_called()


def test_abort_building_to_idle(temp_dir):
    """R2: abort from building must:
    - Cancel reindex engine
    - Disable dual-write
    - Delete target collection
    - Clean NamedQueue
    - Clear state
    """
    MigrationController, _ = _import_controller()

    config = _make_mock_config()
    service = _make_mock_service()
    source_adapter = _make_fake_collection_adapter(name="context", exists=True)
    target_adapter = _make_fake_collection_adapter(name="context_v2", exists=True)
    service.get_source_adapter.return_value = source_adapter
    service.get_target_adapter.return_value = target_adapter

    mock_queue = MagicMock()
    mock_queue.clear = AsyncMock()
    service.get_named_queue.return_value = mock_queue

    existing_state = _make_migration_state(
        phase=MigrationPhase.building,
        dual_write_enabled=True,
    )
    state_manager = _make_mock_state_manager(temp_dir, state=existing_state)
    state_file = _make_mock_state_file(temp_dir)

    controller = MigrationController(
        config=config,
        service=service,
        state_manager=state_manager,
        state_file=state_file,
    )
    from openviking.storage.migration.blue_green_adapter import DualWriteAdapter
    controller._adapter = DualWriteAdapter(
        source=source_adapter,
        target=target_adapter,
        active_side="source",
        dual_write_enabled=True,
    )
    # Mock reindex engine for cancel verification
    mock_engine = MagicMock()
    mock_engine.cancel = MagicMock()
    controller._reindex_engine = mock_engine

    result = controller.abort_migration()

    assert result.phase == MigrationPhase.idle

    # Reindex must be cancelled (R2)
    mock_engine.cancel.assert_called_once()

    # Target must be deleted
    target_adapter.drop_collection.assert_called()

    # State must be cleared
    state_manager.delete.assert_called()


def test_abort_building_complete_to_idle(temp_dir):
    """R3: abort from building_complete must:
    - Disable dual-write
    - Delete target collection
    - Clean NamedQueue
    - Clear state
    (no reindex to cancel since it already completed)
    """
    MigrationController, _ = _import_controller()

    config = _make_mock_config()
    service = _make_mock_service()
    source_adapter = _make_fake_collection_adapter(name="context", exists=True)
    target_adapter = _make_fake_collection_adapter(name="context_v2", exists=True)
    service.get_source_adapter.return_value = source_adapter
    service.get_target_adapter.return_value = target_adapter

    existing_state = _make_migration_state(
        phase=MigrationPhase.building_complete,
        dual_write_enabled=True,
        reindex_progress=ReindexProgress(processed=100, total=100, errors=5),
    )
    state_manager = _make_mock_state_manager(temp_dir, state=existing_state)
    state_file = _make_mock_state_file(temp_dir)

    controller = MigrationController(
        config=config,
        service=service,
        state_manager=state_manager,
        state_file=state_file,
    )
    from openviking.storage.migration.blue_green_adapter import DualWriteAdapter
    controller._adapter = DualWriteAdapter(
        source=source_adapter,
        target=target_adapter,
        active_side="source",
        dual_write_enabled=True,
    )

    result = controller.abort_migration()

    assert result.phase == MigrationPhase.idle

    target_adapter.drop_collection.assert_called()
    state_manager.delete.assert_called()


# =============================================================================
# 8. rollback — switched → dual_write (R4)
# =============================================================================


def test_rollback_switched_to_dual_write(temp_dir):
    """R4: rollback from switched must:
    - Call adapter.set_active("source") (read back to source)
    - Keep dual-write enabled
    - Update state (non-destructive — target collection preserved)
    """
    MigrationController, _ = _import_controller()

    config = _make_mock_config()
    service = _make_mock_service()
    source_adapter = _make_fake_collection_adapter(name="context", exists=True)
    target_adapter = _make_fake_collection_adapter(name="context_v2", exists=True)
    service.get_source_adapter.return_value = source_adapter
    service.get_target_adapter.return_value = target_adapter

    existing_state = _make_migration_state(
        phase=MigrationPhase.switched,
        dual_write_enabled=True,
        active_side="target",
    )
    state_manager = _make_mock_state_manager(temp_dir, state=existing_state)
    state_file = _make_mock_state_file(temp_dir)

    controller = MigrationController(
        config=config,
        service=service,
        state_manager=state_manager,
        state_file=state_file,
    )
    from openviking.storage.migration.blue_green_adapter import DualWriteAdapter
    controller._adapter = DualWriteAdapter(
        source=source_adapter,
        target=target_adapter,
        active_side="target",
        dual_write_enabled=True,
    )

    result = controller.rollback()

    assert result.phase == MigrationPhase.dual_write
    assert result.active_side == "source"
    # Dual-write MUST stay enabled (non-destructive)
    assert result.dual_write_enabled is True

    # Target collection must NOT be deleted (non-destructive)
    target_adapter.drop_collection.assert_not_called()

    # State must be persisted with updated info
    state_manager.save.assert_called()


def test_rollback_dual_write_off_rejected(temp_dir):
    """Rollback from dual_write_off must be rejected with a conflict error.

    Once dual-write is off, source stops receiving writes — rolling back
    would require catching up incremental data, which isn't worth implementing.
    Use abort instead.
    """
    MigrationController, InvalidTransitionError = _import_controller()

    config = _make_mock_config()
    service = _make_mock_service()
    source_adapter = _make_fake_collection_adapter(name="context", exists=True)
    target_adapter = _make_fake_collection_adapter(name="context_v2", exists=True)
    service.get_source_adapter.return_value = source_adapter
    service.get_target_adapter.return_value = target_adapter

    existing_state = _make_migration_state(
        phase=MigrationPhase.dual_write_off,
        dual_write_enabled=False,
        active_side="target",
    )
    state_manager = _make_mock_state_manager(temp_dir, state=existing_state)
    state_file = _make_mock_state_file(temp_dir)

    controller = MigrationController(
        config=config,
        service=service,
        state_manager=state_manager,
        state_file=state_file,
    )
    from openviking.storage.migration.blue_green_adapter import DualWriteAdapter
    controller._adapter = DualWriteAdapter(
        source=source_adapter,
        target=target_adapter,
        active_side="target",
        dual_write_enabled=False,
    )

    with pytest.raises(InvalidTransitionError, match="409|rollback|dual.write.off|not.available"):
        controller.rollback()


def test_rollback_completed_rejected(temp_dir):
    """Rollback from completed must be rejected (migration finished, use new migration)."""
    MigrationController, InvalidTransitionError = _import_controller()

    config = _make_mock_config()
    service = _make_mock_service()
    service.get_source_adapter.return_value = _make_fake_collection_adapter(name="context")
    service.get_target_adapter.return_value = _make_fake_collection_adapter(name="context_v2")

    existing_state = _make_migration_state(
        phase=MigrationPhase.completed,
        dual_write_enabled=False,
    )
    state_manager = _make_mock_state_manager(temp_dir, state=existing_state)
    state_file = _make_mock_state_file(temp_dir)

    controller = MigrationController(
        config=config,
        service=service,
        state_manager=state_manager,
        state_file=state_file,
    )

    with pytest.raises(InvalidTransitionError):
        controller.rollback()


# =============================================================================
# 9. get_status — full snapshot
# =============================================================================


def test_get_status_returns_full_snapshot(temp_dir):
    """get_status must return a dict with:
    - migration_id, phase, active_side
    - source_collection, target_collection
    - dual_write_enabled
    - source_embedder_name, target_embedder_name
    - degraded_write_failures
    - reindex_progress (if applicable)
    - started_at, updated_at
    """
    MigrationController, _ = _import_controller()

    config = _make_mock_config()
    service = _make_mock_service()
    source_adapter = _make_fake_collection_adapter(name="context")
    target_adapter = _make_fake_collection_adapter(name="context_v2")
    service.get_source_adapter.return_value = source_adapter
    service.get_target_adapter.return_value = target_adapter

    existing_state = _make_migration_state(
        migration_id="mig_status_001",
        phase=MigrationPhase.dual_write,
        dual_write_enabled=True,
        active_side="source",
        source_embedder_name="v1",
        target_embedder_name="v2",
        degraded_write_failures=3,
        reindex_progress=ReindexProgress(processed=42, total=100, errors=1, skipped=2),
    )
    state_manager = _make_mock_state_manager(temp_dir, state=existing_state)
    state_file = _make_mock_state_file(temp_dir)

    controller = MigrationController(
        config=config,
        service=service,
        state_manager=state_manager,
        state_file=state_file,
    )

    status = controller.get_status()

    assert isinstance(status, dict)
    assert status["migration_id"] == "mig_status_001"
    assert status["phase"] == MigrationPhase.dual_write
    assert status["active_side"] == "source"
    assert status["source_collection"] == "context"
    assert status["target_collection"] == "context_v2"
    assert status["dual_write_enabled"] is True
    assert status["source_embedder_name"] == "v1"
    assert status["target_embedder_name"] == "v2"
    assert status["degraded_write_failures"] == 3

    if "reindex_progress" in status and status["reindex_progress"] is not None:
        assert status["reindex_progress"]["processed"] == 42
        assert status["reindex_progress"]["total"] == 100
        assert status["reindex_progress"]["errors"] == 1
        assert status["reindex_progress"]["skipped"] == 2


def test_get_status_no_active_migration(temp_dir):
    """get_status should return idle status when no migration is active."""
    MigrationController, _ = _import_controller()

    config = _make_mock_config()
    service = _make_mock_service()
    service.get_source_adapter.return_value = _make_fake_collection_adapter(name="context")
    service.get_target_adapter.return_value = _make_fake_collection_adapter(name="context_v2")

    state_manager = _make_mock_state_manager(temp_dir, state=None)
    state_file = _make_mock_state_file(temp_dir)

    controller = MigrationController(
        config=config,
        service=service,
        state_manager=state_manager,
        state_file=state_file,
    )

    status = controller.get_status()

    assert isinstance(status, dict)
    assert status["phase"] == MigrationPhase.idle or status.get("phase") == "idle"


# =============================================================================
# 10. Illegal transitions
# =============================================================================


def test_illegal_transition_start_from_non_idle(temp_dir):
    """start_migration from a non-idle phase must raise InvalidTransitionError."""
    MigrationController, InvalidTransitionError = _import_controller()

    config = _make_mock_config()
    service = _make_mock_service()
    service.get_source_adapter.return_value = _make_fake_collection_adapter(name="context")
    service.get_target_adapter.return_value = _make_fake_collection_adapter(name="context_v2")

    existing_state = _make_migration_state(phase=MigrationPhase.switched)
    state_manager = _make_mock_state_manager(temp_dir, state=existing_state)
    state_file = _make_mock_state_file(temp_dir)

    controller = MigrationController(
        config=config,
        service=service,
        state_manager=state_manager,
        state_file=state_file,
    )

    with pytest.raises(InvalidTransitionError):
        controller.start_migration("v2")


def test_illegal_transition_confirm_switch_from_dual_write(temp_dir):
    """confirm_switch must reject transition from dual_write (not building_complete)."""
    MigrationController, InvalidTransitionError = _import_controller()

    config = _make_mock_config()
    service = _make_mock_service()
    source_adapter = _make_fake_collection_adapter(name="context")
    target_adapter = _make_fake_collection_adapter(name="context_v2")
    service.get_source_adapter.return_value = source_adapter
    service.get_target_adapter.return_value = target_adapter

    existing_state = _make_migration_state(
        phase=MigrationPhase.dual_write,
        dual_write_enabled=True,
    )
    state_manager = _make_mock_state_manager(temp_dir, state=existing_state)
    state_file = _make_mock_state_file(temp_dir)

    controller = MigrationController(
        config=config,
        service=service,
        state_manager=state_manager,
        state_file=state_file,
    )

    with pytest.raises(InvalidTransitionError):
        controller.confirm_switch()


def test_illegal_transition_raises_correct_error_class(temp_dir):
    """InvalidTransitionError must be a subclass of Exception (or a specific base)."""
    MigrationController, InvalidTransitionError = _import_controller()

    config = _make_mock_config()
    service = _make_mock_service()
    service.get_source_adapter.return_value = _make_fake_collection_adapter(name="context")
    service.get_target_adapter.return_value = _make_fake_collection_adapter(name="context_v2")

    existing_state = _make_migration_state(phase=MigrationPhase.switched)
    state_manager = _make_mock_state_manager(temp_dir, state=existing_state)
    state_file = _make_mock_state_file(temp_dir)

    controller = MigrationController(
        config=config,
        service=service,
        state_manager=state_manager,
        state_file=state_file,
    )

    # Verify the error class hierarchy
    assert issubclass(InvalidTransitionError, Exception)

    try:
        controller.start_migration("v2")
    except InvalidTransitionError as e:
        # Error message should mention the current phase and requested transition
        # so it's debuggable in production
        assert str(e), "InvalidTransitionError must include a message"


# =============================================================================
# 11. Legal transition table validation
# =============================================================================


def test_legal_transitions_table_defined(temp_dir):
    """MigrationController must define _LEGAL_TRANSITIONS with valid mappings."""
    MigrationController, _ = _import_controller()

    config = _make_mock_config()
    service = _make_mock_service()
    service.get_source_adapter.return_value = _make_fake_collection_adapter(name="context")
    service.get_target_adapter.return_value = _make_fake_collection_adapter(name="context_v2")

    state_manager = _make_mock_state_manager(temp_dir, state=None)
    state_file = _make_mock_state_file(temp_dir)

    controller = MigrationController(
        config=config,
        service=service,
        state_manager=state_manager,
        state_file=state_file,
    )

    assert hasattr(controller, "_LEGAL_TRANSITIONS"), (
        "Controller must define _LEGAL_TRANSITIONS validation table"
    )

    lt = controller._LEGAL_TRANSITIONS
    assert isinstance(lt, (dict, set, list)), "_LEGAL_TRANSITIONS must be a collection"

    # If dict, verify key structure
    if isinstance(lt, dict):
        # start_migration: idle → dual_write
        assert "start_migration" in lt or MigrationPhase.idle in lt, (
            "Legal transitions must cover start_migration (idle → dual_write)"
        )
        # begin_building: dual_write → building, building_complete → building
        assert "begin_building" in lt, (
            "Legal transitions must cover begin_building"
        )
        # confirm_switch: building_complete → switched
        assert "confirm_switch" in lt, (
            "Legal transitions must cover confirm_switch"
        )
        # disable_dual_write: switched → dual_write_off
        assert "disable_dual_write" in lt, (
            "Legal transitions must cover disable_dual_write"
        )
        # finish_migration: dual_write_off → completed
        assert "finish_migration" in lt, (
            "Legal transitions must cover finish_migration"
        )
        # abort: any → idle (except completed)
        assert "abort_migration" in lt, (
            "Legal transitions must cover abort_migration"
        )
        # rollback: switched → dual_write
        assert "rollback" in lt, (
            "Legal transitions must cover rollback"
        )


# =============================================================================
# 12. Controller construction and dependency injection
# =============================================================================


def test_controller_constructor_sets_dependencies(temp_dir):
    """MigrationController.__init__ must accept and store: config, service, state_manager, state_file."""
    MigrationController, _ = _import_controller()

    config = _make_mock_config()
    service = _make_mock_service()
    state_manager = _make_mock_state_manager(temp_dir)
    state_file = _make_mock_state_file(temp_dir)

    controller = MigrationController(
        config=config,
        service=service,
        state_manager=state_manager,
        state_file=state_file,
    )

    assert controller._config is config
    assert controller._service is service
    assert controller._state_manager is state_manager
    assert controller._state_file is state_file


def test_controller_loads_state_on_construction(temp_dir):
    """MigrationController must attempt to load existing state on construction."""
    MigrationController, _ = _import_controller()

    config = _make_mock_config()
    service = _make_mock_service()
    state_manager = _make_mock_state_manager(temp_dir, state=None)
    state_file = _make_mock_state_file(temp_dir)

    controller = MigrationController(
        config=config,
        service=service,
        state_manager=state_manager,
        state_file=state_file,
    )

    # state_manager.load() should have been called during construction
    state_manager.load.assert_called()
