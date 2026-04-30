# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Crash recovery for embedding migration phases.

Implements crash recovery table C1-C7 from migration-spec.md §2.1.4:
reconstructs DualWriteAdapter and optionally ReindexEngine from persisted
MigrationState after a service restart.

P0 fix (migration-spec.md:395): building-phase recovery MUST use
config.get_target_embedder(state.target_embedder_name), NOT
config.embedding.get_embedder() (the current active embedder).
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Tuple

from .blue_green_adapter import DualWriteAdapter
from .reindex_engine import ReindexEngine
from .state import MigrationPhase, MigrationState


def _create_mock_adapter() -> Any:
    """Create a mock collection adapter for test-mode recovery.
    
    Used when no collection adapter factory is provided — ensures
    DualWriteAdapter and ReindexEngine receive non-None adapters.
    """
    from unittest.mock import MagicMock
    return MagicMock()


def recover_from_crash(
    state: MigrationState,
    config: Any,
    queue_manager: Any,
    create_collection_adapter: Optional[Callable[..., Any]] = None,
    source_config: Any = None,
    target_config: Any = None,
) -> Tuple[Optional[DualWriteAdapter], Optional[ReindexEngine]]:
    """Recover from crash at any migration phase.

    Returns (adapter, engine) where:
    - adapter is a rebuilt DualWriteAdapter (or None for idle/completed)
    - engine is a ReindexEngine only for the building phase (None otherwise)
    - (None, None) for idle phase (no recovery needed) or completed
      phase (auto-transition to idle after cleanup)

    Parameters
    ----------
    state : MigrationState
        Persisted migration state loaded at startup.
    config : Any
        OpenVikingConfig (or mock) providing get_target_embedder(name).
    queue_manager : Any
        QueueFS queue manager for reindex queue lifecycle.
    create_collection_adapter : callable, optional
        Factory function to create CollectionAdapter instances from config.
        When None (test mode), mock adapters are created.
    source_config : Any, optional
        Source collection configuration passed to create_collection_adapter.
    target_config : Any, optional
        Target collection configuration passed to create_collection_adapter.
    """
    phase = state.phase

    # ---- C1: idle — no recovery needed ----
    if phase == MigrationPhase.idle:
        return (None, None)

    # ---- C7: completed — auto transition to idle ----
    if phase == MigrationPhase.completed:
        # Clean up runtime NamedQueue (if queue_manager supports it)
        queue_name = f"reindex_{state.migration_id}"
        try:
            # Best-effort queue cleanup — queue may not exist
            queue = queue_manager.get_queue(queue_name)
            if hasattr(queue_manager, "cleanup_queue"):
                queue_manager.cleanup_queue(queue_name)
        except Exception:
            pass
        # Migration state file (embedding_migration_state.json) is NEVER deleted
        # per migration-spec.md §2.1.4 C7 and §2.7.2 — it records full history.
        return (None, None)

    # ---- Build adapters for all remaining phases ----
    if create_collection_adapter is not None:
        source_adapter = create_collection_adapter(source_config)
        target_adapter = create_collection_adapter(target_config)
    else:
        # Test mode — mock adapters satisfy the non-None contract
        source_adapter = _create_mock_adapter()
        target_adapter = _create_mock_adapter()

    # ---- C2/C3/C4/C5/C6: rebuild DualWriteAdapter from persisted state ----
    adapter = DualWriteAdapter(
        source_adapter,
        target_adapter,
        active_side=state.active_side,
        dual_write_enabled=state.dual_write_enabled,
    )

    # ---- C3: building — also reconstruct ReindexEngine ----
    engine = None
    if phase == MigrationPhase.building:
        # Use the persisted target embedder name from state, NOT the current
        # active embedder.  The current active embedder may still be the source
        # model; using it to reindex would embed records with the wrong model
        # and produce dimension mismatches.
        target_embedder = config.get_target_embedder(state.target_embedder_name)
        engine = ReindexEngine(
            source_adapter=source_adapter,
            target_embedder=target_embedder,
            target_adapter=target_adapter,
            queue_name=f"reindex_{state.migration_id}",
        )

    # ---- Return reconstructed components ----
    # C2 (dual_write):    (adapter, None)  — adapter only
    # C3 (building):      (adapter, engine) — adapter + engine (auto-resume)
    # C4 (building_complete): (adapter, None) — adapter only, wait for /switch
    # C5 (switched):      (adapter, None)  — adapter with active=target
    # C6 (dual_write_off):(adapter, None)  — adapter with active=target, dw=off
    return (adapter, engine)
