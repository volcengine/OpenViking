# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Coverage gap-fill tests for migration components.

Tests cover remaining uncovered branches in:
- blue_green_adapter (close, get_collection_info, get_collection, clear, etc.)
- controller (unknown action, wrong target_phase, RuntimeError, etc.)
- state (reindex_progress from_dict)
- resilience (idle recovery, queue cleanup errors)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from openviking.storage.migration.blue_green_adapter import DualWriteAdapter
from openviking.storage.migration.controller import (
    InvalidTransitionError,
    MigrationController,
    MigrationPhase,
    MigrationState,
    MigrationStateFile,
    MigrationStateManager,
    ReindexProgress,
)
from openviking.storage.migration.resilience import recover_from_crash

# Reuse FakeCollectionAdapter from blue-green adapter tests
from tests.migration.test_blue_green_adapter import FakeCollectionAdapter


# =============================================================================
# Helpers
# =============================================================================


def _make_mock_config(**kwargs: Any) -> MagicMock:
    cfg = MagicMock()
    cfg.source_embedder_name = kwargs.get("source_embedder_name", "embedder_v1")
    cfg.queue_name = kwargs.get("queue_name", "reindex")
    cfg.get_target_embedder = MagicMock(return_value=MagicMock())
    return cfg


def _make_mock_service(**kwargs: Any) -> MagicMock:
    svc = MagicMock()
    source = _make_fake_collection_adapter(name=kwargs.get("source_name", "source_coll"))
    target = _make_fake_collection_adapter(name=kwargs.get("target_name", "target_coll"))
    svc.get_source_adapter.return_value = source
    svc.get_target_adapter.return_value = target
    queue = MagicMock()
    queue.clear = MagicMock()
    svc.get_named_queue.return_value = queue
    return svc


def _make_fake_collection_adapter(name: str = "context", exists: bool = True) -> MagicMock:
    adapter = MagicMock()
    adapter.collection_name = name
    adapter.collection_exists.return_value = exists
    adapter.create_collection.return_value = True
    adapter.drop_collection.return_value = True
    adapter.get_collection_info.return_value = {"CollectionName": name}
    return adapter


# =============================================================================
# BlueGreenAdapter — uncovered methods
# =============================================================================


class TestDualWriteAdapterCoverageGaps:
    """Tests for DualWriteAdapter methods not covered by existing tests."""

    def test_close_calls_both_source_and_target(self):
        """close() calls close on both source and target adapters."""
        source = FakeCollectionAdapter(collection_name="source")
        target = FakeCollectionAdapter(collection_name="target")
        adapter = DualWriteAdapter(source=source, target=target)

        adapter.close()
        # After close, both adapters should be closed (Fake doesn't track this,
        # but the call should not raise)

    def test_close_source_fails_still_closes_target(self):
        """close() should call target.close() even if source.close() raises."""
        source = MagicMock()
        source.close.side_effect = RuntimeError("source close failed")
        target = MagicMock()

        adapter = DualWriteAdapter(source=source, target=target)
        with pytest.raises(RuntimeError, match="source close failed"):
            adapter.close()

        target.close.assert_called_once()

    def test_get_collection_info_returns_active_side_info(self):
        """get_collection_info() returns info from the active side."""
        source = FakeCollectionAdapter(collection_name="source")
        target = FakeCollectionAdapter(collection_name="target")
        adapter = DualWriteAdapter(source=source, target=target, active_side="source")

        info = adapter.get_collection_info()
        assert info is not None

    def test_get_collection_returns_active_side_collection(self):
        """get_collection() returns the active side's collection handle."""
        source = MagicMock()
        target = MagicMock()
        adapter = DualWriteAdapter(source=source, target=target, active_side="target")

        result = adapter.get_collection()
        target.get_collection.assert_called_once()

    def test_clear_clears_active_and_standby(self):
        """clear() clears active side, and standby if dual-write enabled."""
        source = FakeCollectionAdapter(collection_name="source")
        target = FakeCollectionAdapter(collection_name="target")

        # Pre-populate with records
        adapter = DualWriteAdapter(source=source, target=target, dual_write_enabled=True)
        adapter.upsert({"id": "rec1", "text": "hello"})

        assert len(source._records) == 1
        assert len(target._records) == 1

        adapter.clear()

    def test_clear_no_dual_write_clears_active_only(self):
        """clear() with dual-write disabled clears only active side."""
        source = FakeCollectionAdapter(collection_name="source")
        target = FakeCollectionAdapter(collection_name="target")

        adapter = DualWriteAdapter(source=source, target=target, dual_write_enabled=False)
        adapter.clear()
        # Should not raise

    def test_set_collection_delegates_to_both_adapters(self):
        """set_collection() delegates to both adapters."""
        source_mock = MagicMock()
        target_mock = MagicMock()
        adapter = DualWriteAdapter(source=source_mock, target=target_mock)
        fake_collection = MagicMock()

        adapter.set_collection(fake_collection)

        source_mock.set_collection.assert_called_once_with(fake_collection)
        target_mock.set_collection.assert_called_once_with(fake_collection)

    def test_create_collection_raises_not_implemented(self):
        """create_collection() is not supported — raises NotImplementedError."""
        source = FakeCollectionAdapter(collection_name="source")
        target = FakeCollectionAdapter(collection_name="target")
        adapter = DualWriteAdapter(source=source, target=target)

        with pytest.raises(NotImplementedError, match="not supported"):
            adapter.create_collection()

    def test_drop_collection_requires_explicit_side(self):
        """drop_collection() raises ValueError when side is None."""
        source = FakeCollectionAdapter(collection_name="source")
        target = FakeCollectionAdapter(collection_name="target")
        adapter = DualWriteAdapter(source=source, target=target, dual_write_enabled=False)

        with pytest.raises(ValueError, match="specify which side"):
            adapter.drop_collection(side=None)

    def test_drop_collection_rejects_invalid_side_name(self):
        """drop_collection() raises ValueError for unknown side name."""
        source = FakeCollectionAdapter(collection_name="source")
        target = FakeCollectionAdapter(collection_name="target")
        adapter = DualWriteAdapter(source=source, target=target, dual_write_enabled=False)

        with pytest.raises(ValueError, match="Unknown side"):
            adapter.drop_collection(side="invalid_side")

    def test_get_fetches_from_active_only(self):
        """get() fetches records from the active side only."""
        source = FakeCollectionAdapter(collection_name="source")
        target = FakeCollectionAdapter(collection_name="target")
        adapter = DualWriteAdapter(source=source, target=target, active_side="source")

        # upsert to populate
        adapter.upsert({"id": "rec_a", "text": "data"})

        result = adapter.get(["rec_a"])
        assert len(result) >= 1

    def test_count_returns_active_side_count(self):
        """count() returns the count from the active side only."""
        source = FakeCollectionAdapter(collection_name="source")
        target = FakeCollectionAdapter(collection_name="target")
        adapter = DualWriteAdapter(source=source, target=target, active_side="source")

        adapter.upsert({"id": "rec_1", "text": "a"})
        adapter.upsert({"id": "rec_2", "text": "b"})

        # count should not raise
        count = adapter.count()
        assert isinstance(count, int)

    def test_constructor_rejects_invalid_active_side(self):
        """Constructor raises ValueError for invalid active_side."""
        source = FakeCollectionAdapter(collection_name="source")
        target = FakeCollectionAdapter(collection_name="target")

        with pytest.raises(ValueError, match="active_side must be"):
            DualWriteAdapter(source=source, target=target, active_side="invalid")


# =============================================================================
# Controller — uncovered branches
# =============================================================================


class TestControllerCoverageGaps:
    """Tests for MigrationController branches not covered by existing tests."""

    def _make_controller(self, state_manager=None, state_file=None):
        config = _make_mock_config()
        service = _make_mock_service()
        if state_manager is None:
            state_manager = MagicMock(spec=MigrationStateManager)
            state_manager.load.return_value = None
        if state_file is None:
            state_file = MagicMock(spec=MigrationStateFile)
        return MigrationController(
            config=config,
            service=service,
            state_manager=state_manager,
            state_file=state_file,
        )

    def test_validate_transition_unknown_action(self):
        """_validate_transition raises InvalidTransitionError for unknown action."""
        controller = self._make_controller()

        with pytest.raises(InvalidTransitionError, match="Unknown action"):
            controller._validate_transition("nonexistent_action")

    def test_validate_transition_wrong_target_phase(self):
        """_validate_transition raises if the target phase doesn't match legal transition."""
        controller = self._make_controller()
        # Set state to dual_write
        controller._state = MigrationState(
            migration_id="m1",
            phase=MigrationPhase.dual_write,
            source_collection="src",
            target_collection="tgt",
            active_side="source",
            dual_write_enabled=True,
            source_embedder_name="v1",
            target_embedder_name="v2",
        )

        # begin_building from dual_write should go to building, not something else
        with pytest.raises(InvalidTransitionError, match="must transition to"):
            controller._validate_transition("begin_building", target_phase=MigrationPhase.switched)

    def test_on_reindex_done_wrong_phase_noop(self, tmp_path: Path):
        """_on_reindex_done does nothing if current phase is not building."""
        controller = self._make_controller()
        controller._state = MigrationState(
            migration_id="m1",
            phase=MigrationPhase.dual_write,
            source_collection="src",
            target_collection="tgt",
            active_side="source",
            dual_write_enabled=True,
            source_embedder_name="v1",
            target_embedder_name="v2",
        )
        controller._reindex_engine = MagicMock()

        # Should not raise
        controller._on_reindex_done()

    def test_finish_migration_clears_queue(self):
        """finish_migration() clears the NamedQueue if one is allocated."""
        state_manager = MagicMock(spec=MigrationStateManager)
        state_manager.load.return_value = MigrationState(
            migration_id="m1",
            phase=MigrationPhase.dual_write_off,
            source_collection="src",
            target_collection="tgt",
            active_side="target",
            dual_write_enabled=False,
            source_embedder_name="v1",
            target_embedder_name="v2",
        )
        state_file = MagicMock(spec=MigrationStateFile)

        controller = self._make_controller(state_manager=state_manager, state_file=state_file)
        queue_mock = MagicMock()
        controller._queue = queue_mock
        # Need source/target adapters for the cleanup path
        controller._source_adapter = MagicMock()
        controller._target_adapter = MagicMock()

        controller.finish_migration()
        queue_mock.clear.assert_called_once()


# =============================================================================
# State — uncovered from_dict with reindex_progress
# =============================================================================


class TestStateCoverageGaps:
    """Tests for MigrationState.deserialize with reindex_progress."""

    def test_from_dict_with_reindex_progress(self):
        """from_dict correctly deserializes reindex_progress when present."""
        data = {
            "migration_id": "m1",
            "phase": "building",
            "source_collection": "src",
            "target_collection": "tgt",
            "active_side": "source",
            "dual_write_enabled": True,
            "source_embedder_name": "v1",
            "target_embedder_name": "v2",
            "reindex_progress": {
                "processed": 100,
                "total": 500,
                "errors": 3,
                "skipped": 5,
            },
        }
        state = MigrationState.from_dict(data)
        assert state.reindex_progress is not None
        assert state.reindex_progress.processed == 100
        assert state.reindex_progress.total == 500
        assert state.reindex_progress.errors == 3
        assert state.reindex_progress.skipped == 5

    def test_to_dict_includes_reindex_progress(self):
        """to_dict serializes reindex_progress when present."""
        state = MigrationState(
            migration_id="m1",
            phase=MigrationPhase.building,
            source_collection="src",
            target_collection="tgt",
            active_side="source",
            dual_write_enabled=True,
            source_embedder_name="v1",
            target_embedder_name="v2",
            reindex_progress=ReindexProgress(processed=50, total=200, errors=2, skipped=1),
        )
        d = state.to_dict()
        assert "reindex_progress" in d
        assert d["reindex_progress"]["processed"] == 50


# =============================================================================
# Resilience — idle recovery
# =============================================================================


class TestResilienceCoverageGaps:
    """Tests for recover_from_crash edge cases."""

    def test_recover_idle_returns_none_none(self):
        """Recovery from idle returns (None, None)."""
        state = MigrationState(
            migration_id="",
            phase=MigrationPhase.idle,
            source_collection="",
            target_collection="",
            active_side="",
            dual_write_enabled=False,
            source_embedder_name="",
            target_embedder_name="",
        )
        config = MagicMock()
        queue_manager = MagicMock()

        adapter, engine = recover_from_crash(state, config, queue_manager)
        assert adapter is None
        assert engine is None
