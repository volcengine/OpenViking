# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""MigrationController — state machine orchestrating embedding migration transitions.

Implements the 7-phase forward migration lifecycle (T1-T7) plus rollback (R1-R4)
and abort at every phase.  All transition legality is enforced via _LEGAL_TRANSITIONS.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .blue_green_adapter import DualWriteAdapter
from .reindex_engine import ReindexEngine
from .state import (
    MigrationPhase,
    MigrationState,
    MigrationStateFile,
    MigrationStateManager,
    ReindexProgress,
)


class InvalidTransitionError(Exception):
    """Raised when an illegal state transition is attempted.

    Subclass of ``Exception`` so callers can catch generically or specifically.
    """


class MigrationController:
    """State-machine orchestrator for blue-green embedding migration.

    Constructor dependencies are injected with keyword arguments:
        - *config*: MigratorConfig-like object with ``get_target_embedder()``
          and embedder name attributes.
        - *service*: MigratorService-like object providing adapters and queues.
        - *state_manager*: ``MigrationStateManager`` for runtime state persistence.
        - *state_file*: ``MigrationStateFile`` for permanent migration history.

    On construction the controller attempts to load an existing runtime state
    via ``state_manager.load()`` so that recovery after a crash is seamless.

    All public transition methods return the new ``MigrationState``.
    """

    # Allowed transitions: action_name → {from_phase → to_phase}
    _LEGAL_TRANSITIONS: Dict[str, Dict[MigrationPhase, MigrationPhase]] = {
        "start_migration": {
            MigrationPhase.idle: MigrationPhase.dual_write,
        },
        "begin_building": {
            MigrationPhase.dual_write: MigrationPhase.building,
            MigrationPhase.building_complete: MigrationPhase.building,
        },
        "confirm_switch": {
            MigrationPhase.building_complete: MigrationPhase.switched,
        },
        "disable_dual_write": {
            MigrationPhase.switched: MigrationPhase.dual_write_off,
        },
        "finish_migration": {
            MigrationPhase.dual_write_off: MigrationPhase.completed,
        },
        "abort_migration": {
            MigrationPhase.dual_write: MigrationPhase.idle,
            MigrationPhase.building: MigrationPhase.idle,
            MigrationPhase.building_complete: MigrationPhase.idle,
            MigrationPhase.switched: MigrationPhase.idle,
            MigrationPhase.dual_write_off: MigrationPhase.idle,
        },
        "rollback": {
            MigrationPhase.switched: MigrationPhase.dual_write,
        },
    }

    # ------------------------------------------------------------------
    # Constructor
    # ------------------------------------------------------------------

    def __init__(
        self,
        *,
        config: Any,
        service: Any,
        state_manager: MigrationStateManager,
        state_file: MigrationStateFile,
    ) -> None:
        self._config = config
        self._service = service
        self._state_manager = state_manager
        self._state_file = state_file

        # Lazy-initialised by start_migration / begin_building
        self._adapter: Optional[Any] = None
        self._source_adapter: Optional[Any] = None
        self._target_adapter: Optional[Any] = None
        self._reindex_engine: Optional[ReindexEngine] = None
        self._queue: Any = None
        self._stop_event: Optional[asyncio.Event] = None

        # Load existing state (may be None → idle)
        self._state: Optional[MigrationState] = self._state_manager.load()

    # ------------------------------------------------------------------
    # Internal: transition validation
    # ------------------------------------------------------------------

    def _validate_transition(self, action: str, target_phase: Optional[MigrationPhase] = None) -> None:
        """Raise InvalidTransitionError if *action* is illegal from the current phase.

        If *target_phase* is given, also verify the transition leads to it.
        """
        state = self._state
        current = MigrationPhase.idle if state is None else state.phase

        legal = self._LEGAL_TRANSITIONS.get(action)
        if legal is None:
            raise InvalidTransitionError(
                f"Unknown action '{action}'"
            )
        if current not in legal:
            raise InvalidTransitionError(
                f"Cannot {action} from phase {current.value}"
            )
        if target_phase is not None and legal[current] != target_phase:
            raise InvalidTransitionError(
                f"{action} must transition to {legal[current].value}, not {target_phase.value}"
            )

    # ------------------------------------------------------------------
    # T1: start_migration — idle → dual_write
    # ------------------------------------------------------------------

    def start_migration(self, target_name: str) -> MigrationState:
        """Begin migration by entering dual-write mode.

        Creates the target collection, constructs a ``DualWriteAdapter``
        bridging source and target, and persists the initial
        ``MigrationState`` with embedder names.
        """
        # Guard: reject if an active (non-idle) migration already exists
        if self._state is not None and self._state.phase != MigrationPhase.idle:
            raise InvalidTransitionError(
                f"Active migration already in progress (phase={self._state.phase.value})"
            )

        self._validate_transition("start_migration")

        source_adapter = self._service.get_source_adapter()
        target_adapter = self._service.get_target_adapter()
        self._source_adapter = source_adapter
        self._target_adapter = target_adapter

        # Create target collection if it doesn't already exist
        if not target_adapter.collection_exists():
            target_adapter.create_collection()

        # Construct the dual-write adapter (source=active, dual_write=enabled)
        adapter = DualWriteAdapter(
            source=source_adapter,
            target=target_adapter,
            active_side="source",
            dual_write_enabled=True,
        )
        self._adapter = adapter

        now = datetime.now(timezone.utc).isoformat()
        state = MigrationState(
            migration_id=self._generate_migration_id(),
            phase=MigrationPhase.dual_write,
            source_collection=source_adapter.collection_name,
            target_collection=target_adapter.collection_name,
            active_side="source",
            dual_write_enabled=True,
            source_embedder_name=self._config.source_embedder_name,
            target_embedder_name=target_name,
            degraded_write_failures=0,
            reindex_progress=None,
            started_at=now,
            updated_at=now,
        )
        self._state = state
        self._state_manager.save(state)
        return state

    # ------------------------------------------------------------------
    # T2: begin_building — dual_write / building_complete → building
    # ------------------------------------------------------------------

    def begin_building(self) -> MigrationState:
        """Transition to the building phase.

        Creates the target embedder (using ``state.target_embedder_name`` —
        NOT the config's current active embedder, per C-2 fix), constructs a
        ``ReindexEngine``, and launches the background processing task.

        When called from ``building_complete``, the old NamedQueue is cleaned
        first (rebuild scenario).
        """
        self._validate_transition("begin_building")
        state = self._state
        assert state is not None  # guaranteed by validate_transition for this action

        queue_name = getattr(self._config, "queue_name", "reindex")
        queue = self._service.get_named_queue(queue_name)

        # Rebuild from building_complete: clean the old queue first
        if state.phase == MigrationPhase.building_complete:
            if self._queue is not None:
                self._queue.clear()
            # Fall through — reuse the same queue for the new build

        self._queue = queue

        # Use the persisted target embedder name from state (NOT the current
        # active embedder from config.embedding), so reindex always uses the
        # migration target model rather than the source model.
        target_embedder = self._config.get_target_embedder(state.target_embedder_name)

        engine = ReindexEngine(
            source_adapter=self._source_adapter,
            target_embedder=target_embedder,
            target_adapter=self._target_adapter,
            queue_name=queue_name,
        )
        engine._queue = queue
        self._reindex_engine = engine

        # Start background reindex task if an event loop is running
        self._stop_event = asyncio.Event()
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(engine.process_queue(self._stop_event))
            task.add_done_callback(self._on_reindex_done)
        except RuntimeError:
            pass  # No running loop (e.g. test environment)

        state.phase = MigrationPhase.building
        state.updated_at = datetime.now(timezone.utc).isoformat()
        self._state_manager.save(state)
        return state

    # ------------------------------------------------------------------
    # T3: _on_reindex_done — building → building_complete
    # ------------------------------------------------------------------

    def _on_reindex_done(self, _future: Any = None) -> None:
        """Callback invoked when the background reindex task completes.

        Records the final ``ReindexProgress`` and transitions the phase to
        ``building_complete`` (NOT ``switched`` — that requires explicit
        confirmation).
        """
        state = self._state
        if state is None or state.phase != MigrationPhase.building:
            return

        if self._reindex_engine is not None:
            state.reindex_progress = self._reindex_engine.get_progress()

        state.phase = MigrationPhase.building_complete
        state.updated_at = datetime.now(timezone.utc).isoformat()
        self._state_manager.save(state)

    # ------------------------------------------------------------------
    # T4: confirm_switch — building_complete → switched
    # ------------------------------------------------------------------

    def confirm_switch(self) -> MigrationState:
        """Switch the active read side to the target embedder.

        Validates that reindex errors are below the acceptable threshold
        before performing the switch.  The caller (human or automation)
        should inspect ``get_status()`` before calling this.
        """
        self._validate_transition("confirm_switch")
        state = self._state
        assert state is not None  # guaranteed by validate_transition for this action

        # Error-rate gate: prevent switching if too many reindex errors
        if state.reindex_progress is not None:
            total = state.reindex_progress.total or 1  # avoid / 0
            errors = state.reindex_progress.errors
            error_rate = errors / total
            if error_rate > 0.1:  # 10 % threshold
                raise InvalidTransitionError(
                    f"Error rate {error_rate:.1%} exceeds threshold "
                    f"(errors={errors}/{total})"
                )

        # Perform the switch (target embedder endpoint already exists)
        if self._adapter is not None:
            self._adapter.set_active("target")

        state.phase = MigrationPhase.switched
        state.active_side = "target"
        state.updated_at = datetime.now(timezone.utc).isoformat()
        self._state_manager.save(state)
        return state

    # ------------------------------------------------------------------
    # T5: disable_dual_write — switched → dual_write_off
    # ------------------------------------------------------------------

    def disable_dual_write(self) -> MigrationState:
        """Disable dual-write — writes now go exclusively to target."""
        self._validate_transition("disable_dual_write")
        state = self._state
        assert state is not None  # guaranteed by validate_transition for this action

        if self._adapter is not None:
            self._adapter.set_dual_write(False)

        state.phase = MigrationPhase.dual_write_off
        state.dual_write_enabled = False
        state.updated_at = datetime.now(timezone.utc).isoformat()
        self._state_manager.save(state)
        return state

    # ------------------------------------------------------------------
    # T6+T7: finish_migration — dual_write_off → completed → idle
    # ------------------------------------------------------------------

    def finish_migration(self, confirm_cleanup: bool = False) -> MigrationState:
        """Finalise the migration: update permanent state, clean runtime state.

        Args:
            confirm_cleanup: When ``True``, also drop the (now-unused)
                source collection.
        """
        self._validate_transition("finish_migration")
        state = self._state
        assert state is not None  # guaranteed by validate_transition for this action

        # Update permanent migration state file
        self._state_file.update_current_active(state.target_embedder_name)

        history_entry: Dict[str, Any] = {
            "id": state.migration_id,
            "from_name": state.source_embedder_name,
            "to_name": state.target_embedder_name,
            "status": "completed",
        }
        self._state_file.append_history(history_entry)

        # Optional source cleanup — fall back to service if adapter not injected
        if confirm_cleanup:
            source = self._source_adapter
            if source is None:
                source = self._service.get_source_adapter()
            if source is not None:
                source.drop_collection()

        # Clean up runtime artefacts
        self._state_manager.delete()
        if self._queue is not None:
            self._queue.clear()

        self._state = None
        self._adapter = None
        self._reindex_engine = None
        self._source_adapter = None
        self._target_adapter = None
        self._queue = None

        # Return a synthetic idle state (the system is idle after finishing)
        return MigrationState(
            migration_id="",
            phase=MigrationPhase.idle,
            source_collection="",
            target_collection="",
            active_side="",
            dual_write_enabled=False,
            source_embedder_name="",
            target_embedder_name="",
        )

    # ------------------------------------------------------------------
    # Abort: any → idle (R1, R2, R3, plus switched / dual_write_off)
    # ------------------------------------------------------------------

    def abort_migration(self) -> MigrationState:
        """Abort the migration from any phase.

        Delegates phase-specific cleanup to internal handlers (also
        callable directly via the rollback.py wrappers for testing).
        Rejected for ``completed`` and ``idle``.
        """
        self._validate_transition("abort_migration")
        state = self._state
        assert state is not None  # guaranteed by validate_transition for this action

        phase = state.phase
        if phase == MigrationPhase.dual_write:
            self._handle_abort_dual_write()
        elif phase == MigrationPhase.building:
            self._handle_abort_building()
        elif phase == MigrationPhase.building_complete:
            self._handle_abort_building_complete()
        else:
            # switched / dual_write_off: generic abort
            self._handle_abort_generic()

        return MigrationState(
            migration_id="",
            phase=MigrationPhase.idle,
            source_collection="",
            target_collection="",
            active_side="",
            dual_write_enabled=False,
            source_embedder_name="",
            target_embedder_name="",
        )

    # ------------------------------------------------------------------
    # Internal abort handlers — also the real implementation behind
    # rollback.py wrappers (which exist solely for testability).
    # ------------------------------------------------------------------

    def _handle_abort_dual_write(self) -> None:
        """R1: disable dual-write, drop target, clear runtime state."""
        self._disable_dual_write_safe()
        self._drop_target_safe()
        self._clear_runtime_state()

    def _handle_abort_building(self) -> None:
        """R2: cancel reindex, disable dw, drop target, clear queue, clear state."""
        self._cancel_reindex_engine_safe()
        self._disable_dual_write_safe()
        self._drop_target_safe()
        self._clear_queue_safe()
        self._clear_runtime_state()

    def _handle_abort_building_complete(self) -> None:
        """R3: disable dw, drop target (all reindex data), clear queue, clear state."""
        self._disable_dual_write_safe()
        self._drop_target_safe()
        self._clear_queue_safe()
        self._clear_runtime_state()

    def _handle_abort_generic(self) -> None:
        """Abort from switched / dual_write_off: drop target, clear state."""
        if self._adapter is not None:
            self._adapter.set_dual_write(False)
        if self._target_adapter is not None:
            self._target_adapter.drop_collection()
        self._state_manager.delete()
        self._state = None

    # ------------------------------------------------------------------
    # Rollback: switched → dual_write (R4)
    # ------------------------------------------------------------------

    def rollback(self) -> MigrationState:
        """Non-destructive rollback from switched back to dual_write.

        Active reads switch back to ``source``; dual-write remains enabled.
        The target collection is NOT dropped.
        """
        self._validate_transition("rollback")
        self._handle_rollback_switched()
        assert self._state is not None  # guaranteed by validate_transition
        return self._state

    def _handle_rollback_switched(self) -> None:
        """R4: switch active back to source, keep dw, save state."""
        if self._adapter is not None:
            self._adapter.set_active("source")

        state = self._state
        assert state is not None  # caller guarantees non-None
        state.phase = MigrationPhase.dual_write
        state.active_side = "source"
        # dual_write_enabled stays as-is (should be True)
        state.updated_at = datetime.now(timezone.utc).isoformat()
        self._state_manager.save(state)

    # ------------------------------------------------------------------
    # Safe helper methods for abort / rollback operations
    # ------------------------------------------------------------------

    def _disable_dual_write_safe(self) -> None:
        """Disable dual-write on the adapter (best-effort)."""
        if self._adapter is not None:
            self._adapter.set_dual_write(False)

    def _cancel_reindex_engine_safe(self) -> None:
        """Cancel the reindex engine; swallow failures gracefully."""
        if self._reindex_engine is not None:
            try:
                self._reindex_engine.cancel()
            except Exception:
                pass

    def _drop_target_safe(self) -> None:
        """Drop the target adapter's collection.

        Tries the directly-injected ``_target_adapter`` first; falls back
        to the DualWriteAdapter (dual-write must already be disabled).
        """
        if self._target_adapter is not None:
            self._target_adapter.drop_collection()
        elif self._adapter is not None:
            self._adapter.drop_collection(side="target")

    def _clear_queue_safe(self) -> None:
        """Clear the NamedQueue if one was allocated."""
        if self._queue is not None:
            self._queue.clear()

    def _clear_runtime_state(self) -> None:
        """Delete the runtime state file and null out in-memory state."""
        self._state_manager.delete()
        self._state = None

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """Return a dictionary snapshot of the current migration status."""
        state = self._state
        if state is None:
            return {"phase": MigrationPhase.idle}

        result: Dict[str, Any] = {
            "migration_id": state.migration_id,
            "phase": state.phase,
            "active_side": state.active_side,
            "source_collection": state.source_collection,
            "target_collection": state.target_collection,
            "dual_write_enabled": state.dual_write_enabled,
            "source_embedder_name": state.source_embedder_name,
            "target_embedder_name": state.target_embedder_name,
            "degraded_write_failures": state.degraded_write_failures,
            "started_at": state.started_at,
            "updated_at": state.updated_at,
        }
        if state.reindex_progress is not None:
            result["reindex_progress"] = state.reindex_progress.to_dict()

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_migration_id() -> str:
        """Produce a short unique migration identifier."""
        return f"mig_{uuid.uuid4().hex[:12]}"
