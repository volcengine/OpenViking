# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Migration state types and persistence.

Provides the core data types for the embedding migration state machine:
- MigrationPhase: 7-phase migration lifecycle enum
- ReindexProgress: tracking progress of reindex operations
- MigrationState: runtime migration state dataclass with serialization
- MigrationStateManager: atomic JSON persistence for MigrationState
- MigrationStateFile: permanent migration state file management
"""

import json
import os
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from filelock import FileLock


# =============================================================================
# MigrationPhase
# =============================================================================


class MigrationPhase(str, Enum):
    """Seven-phase migration lifecycle.

    idle -> dual_write -> building -> building_complete ->
    switched -> dual_write_off -> completed
    """

    idle = "idle"
    dual_write = "dual_write"
    building = "building"
    building_complete = "building_complete"
    switched = "switched"
    dual_write_off = "dual_write_off"
    completed = "completed"


class ActiveSide(str, Enum):
    """Which side of the dual-write adapter is the active read side."""

    SOURCE = "source"
    TARGET = "target"


# =============================================================================
# ReindexProgress
# =============================================================================


@dataclass
class ReindexProgress:
    """Progress tracking for reindex operations."""

    processed: int = 0
    total: int = 0
    errors: int = 0
    skipped: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReindexProgress":
        """Deserialize from a plain dictionary."""
        return cls(**data)


# =============================================================================
# MigrationState
# =============================================================================


@dataclass
class MigrationState:
    """Runtime migration state — persisted to migration_runtime_state.json.

    This is a transient file that is cleaned up once migration completes.
    For permanent migration history, see MigrationStateFile.
    """

    migration_id: str
    phase: MigrationPhase
    source_collection: str
    target_collection: str
    active_side: ActiveSide  # "source" or "target"
    dual_write_enabled: bool
    source_embedder_name: str
    target_embedder_name: str
    degraded_write_failures: int = 0
    reindex_progress: Optional[ReindexProgress] = None
    started_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        d = asdict(self)
        d["phase"] = self.phase.value
        if self.reindex_progress is not None:
            d["reindex_progress"] = self.reindex_progress.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MigrationState":
        """Deserialize from a JSON-compatible dictionary."""
        data = dict(data)
        data["phase"] = MigrationPhase(data["phase"])
        if data.get("reindex_progress") is not None:
            data["reindex_progress"] = ReindexProgress.from_dict(data["reindex_progress"])
        return cls(**data)


# =============================================================================
# MigrationStateManager
# =============================================================================


class MigrationStateManager:
    """Atomic JSON persistence for MigrationState.

    Uses tempfile + rename + FileLock to guarantee that a reader never
    sees a partially-written state file.
    """

    STATE_FILE_NAME = "migration_runtime_state.json"

    def __init__(self, state_dir: str):
        self.state_dir = Path(state_dir)
        self.state_file = self.state_dir / self.STATE_FILE_NAME
        self.lock_file = self.state_dir / f"{self.STATE_FILE_NAME}.lock"
        os.makedirs(self.state_dir, exist_ok=True)

    def save(self, state: MigrationState) -> None:
        """Atomic save: write to temp file, rename over target."""
        with FileLock(str(self.lock_file)):
            data = state.to_dict()
            tmp = self.state_file.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp.replace(self.state_file)  # atomic on POSIX; close enough on Windows

    def load(self) -> Optional[MigrationState]:
        """Load state, return None if file doesn't exist or is corrupted."""
        if not self.state_file.exists():
            return None
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return MigrationState.from_dict(data)
        except (json.JSONDecodeError, KeyError, ValueError):
            return None

    def delete(self) -> None:
        """Delete the state file."""
        if self.state_file.exists():
            self.state_file.unlink()


# =============================================================================
# MigrationStateFile
# =============================================================================


class MigrationStateFile:
    """Permanent migration state file (never deleted).

    Tracks current_active embedding config name and full migration history.
    Uses atomic writes for all mutations.
    """

    FILE_NAME = "embedding_migration_state.json"

    def __init__(self, config_dir: str):
        self.config_dir = Path(config_dir)
        self.file_path = self.config_dir / self.FILE_NAME
        self.lock_file = self.config_dir / f"{self.FILE_NAME}.lock"
        os.makedirs(self.config_dir, exist_ok=True)

    def _read_raw(self) -> Dict[str, Any]:
        """Read the raw JSON content from the state file."""
        with open(self.file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_atomic(self, data: Dict[str, Any]) -> None:
        """Atomically write JSON data using tempfile + rename."""
        with FileLock(str(self.lock_file)):
            tmp = self.file_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp.replace(self.file_path)

    def create_initial(self, active_name: str) -> None:
        """Create initial migration state file with no history."""
        data = {
            "version": 1,
            "current_active": active_name,
            "history": [],
        }
        self._write_atomic(data)

    def read(self) -> Dict[str, Any]:
        """Read the migration state file.

        Raises:
            FileNotFoundError: If the state file does not exist.
            json.JSONDecodeError: If the file contains invalid JSON.
        """
        return self._read_raw()

    def _read_write_atomic(self, update_fn) -> None:
        """Read current data, apply *update_fn*, and write atomically.

        The FileLock is held for the entire read-modify-write cycle,
        preventing lost updates from concurrent callers.
        """
        with FileLock(str(self.lock_file)):
            data = self._read_raw()
            update_fn(data)
            tmp = self.file_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp.replace(self.file_path)

    def update_current_active(self, name: str) -> None:
        """Atomically update current_active, preserving other fields."""

        def _update(data: Dict[str, Any]) -> None:
            data["current_active"] = name

        self._read_write_atomic(_update)

    def append_history(self, entry: Dict[str, Any]) -> None:
        """Atomically append a migration history record."""

        def _update(data: Dict[str, Any]) -> None:
            data["history"].append(entry)

        self._read_write_atomic(_update)
