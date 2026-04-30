# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Dual-write adapter for blue-green embedding migration.

Provides DualWriteAdapter — a CollectionAdapter wrapper that mirrors writes
to two backend adapters (source/target) while reading from only the active side.

Key semantics:
- upsert/delete: Active side must succeed; standby is best-effort with retry.
- query/get/count: Routes to active side only.
- collection_exists: Returns True when EITHER side has the collection.
- both_collections_exist: Returns True only when BOTH sides exist.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

from openviking.storage.errors import CollectionNotFoundError
from openviking.storage.expr import FilterExpr
from openviking.storage.vectordb_adapters.base import CollectionAdapter
from openviking_cli.utils import get_logger

from .state import ActiveSide

logger = get_logger(__name__)


class DualWriteAdapter(CollectionAdapter):
    """Wraps two CollectionAdapters, mirroring writes and routing reads.

    Active-side writes are mandatory (exceptions propagate).  Standby-side
    writes are best-effort with up to 2 retries; each permanent standby
    failure increments ``degraded_write_failures``.

    Thread-safety: state-mutating methods (set_active, set_dual_write,
    set_collection) are guarded by ``threading.RLock``.
    """

    # ------------------------------------------------------------------
    # Constructor
    # ------------------------------------------------------------------

    def __init__(
        self,
        source: Any,
        target: Any,
        active_side: str = "source",
        dual_write_enabled: bool = True,
    ) -> None:
        if source is None or target is None:
            raise ValueError("source_adapter and target_adapter must not be None")
        if active_side not in ("source", "target"):
            raise ValueError(
                f"active_side must be 'source' or 'target', got {active_side!r}"
            )

        super().__init__(collection_name="dual_write")
        self.mode = "dual_write"
        self._source = source
        self._target = target
        self._active_side: ActiveSide = ActiveSide(active_side)
        self._dual_write_enabled: bool = dual_write_enabled
        self._degraded_write_failures: int = 0
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Public counters / properties
    # ------------------------------------------------------------------

    @property
    def degraded_write_failures(self) -> int:
        """Number of permanent standby write failures since construction."""
        return self._degraded_write_failures

    # ------------------------------------------------------------------
    # Abstract method implementations (CollectionAdapter contract)
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, config: Any) -> "DualWriteAdapter":
        raise NotImplementedError("DualWriteAdapter must be constructed manually")

    def _load_existing_collection_if_needed(self) -> None:
        # DualWriteAdapter has no collection of its own — collection
        # existence and loading are delegated to the wrapped source/target
        # adapters via the overridden collection_exists() and get_collection().
        raise NotImplementedError(
            "DualWriteAdapter delegates collection loading to wrapped adapters"
        )

    def _create_backend_collection(self, meta: Dict[str, Any]) -> Any:
        raise NotImplementedError(
            "DualWriteAdapter does not support direct collection creation"
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @property
    def _active(self) -> Any:
        """The currently active adapter (source or target)."""
        return self._source if self._active_side == ActiveSide.SOURCE else self._target

    @property
    def _standby(self) -> Any:
        """The currently inactive / standby adapter."""
        return self._target if self._active_side == ActiveSide.SOURCE else self._source

    def _write_to_standby(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        """Best-effort write to standby adapter with retry.

        Attempts up to 3 times (1 initial + 2 retries).  On permanent
        failure, increments ``degraded_write_failures`` and logs a warning.
        """
        standby = self._standby
        method = getattr(standby, method_name)
        max_attempts = 3
        last_exception: Optional[Exception] = None

        for attempt in range(max_attempts):
            try:
                return method(*args, **kwargs)
            except Exception as exc:
                last_exception = exc
                if attempt < max_attempts - 1:
                    time.sleep(0.1)  # Small back-off before retry

        # All attempts exhausted
        self._degraded_write_failures += 1
        logger.warning(
            "Standby write (%s.%s) failed after %d attempts: %s",
            type(standby).__name__,
            method_name,
            max_attempts,
            last_exception,
        )
        return None

    # ------------------------------------------------------------------
    # Core CRUD (overrides CollectionAdapter)
    # ------------------------------------------------------------------

    def upsert(
        self,
        data: Dict[str, Any] | List[Dict[str, Any]],
    ) -> List[str]:
        """Upsert to active side (mandatory), then to standby (best-effort)."""
        ids = self._active.upsert(data)
        if self._dual_write_enabled:
            self._write_to_standby("upsert", data)
        return ids

    def delete(
        self,
        *,
        ids: Optional[List[str]] = None,
        filter: Optional[Dict[str, Any] | FilterExpr] = None,
        limit: int = 100000,
    ) -> int:
        """Delete from active side (mandatory), then from standby (best-effort)."""
        result = self._active.delete(ids=ids, filter=filter, limit=limit)
        if self._dual_write_enabled:
            self._write_to_standby(
                "delete", ids=ids, filter=filter, limit=limit
            )
        return result

    def query(
        self,
        *,
        query_vector: Optional[List[float]] = None,
        sparse_query_vector: Optional[Dict[str, float]] = None,
        filter: Optional[Dict[str, Any] | FilterExpr] = None,
        limit: int = 10,
        offset: int = 0,
        output_fields: Optional[List[str]] = None,
        order_by: Optional[str] = None,
        order_desc: bool = False,
    ) -> List[Dict[str, Any]]:
        """Query the active side only."""
        return self._active.query(
            query_vector=query_vector,
            sparse_query_vector=sparse_query_vector,
            filter=filter,
            limit=limit,
            offset=offset,
            output_fields=output_fields,
            order_by=order_by,
            order_desc=order_desc,
        )

    def get(self, ids: List[str]) -> List[Dict[str, Any]]:
        """Fetch records from active side only."""
        return self._active.get(ids)

    def count(
        self, filter: Optional[Dict[str, Any] | FilterExpr] = None
    ) -> int:
        """Count records on active side only."""
        return self._active.count(filter=filter)

    # ------------------------------------------------------------------
    # Collection management
    # ------------------------------------------------------------------

    def collection_exists(self) -> bool:
        """Return True when EITHER source or target collection exists."""
        return (
            self._source.collection_exists()
            or self._target.collection_exists()
        )

    def both_collections_exist(self) -> bool:
        """Return True only when BOTH source and target collections exist."""
        return (
            self._source.collection_exists()
            and self._target.collection_exists()
        )

    def drop_collection(self, side: Optional[str] = None) -> bool:
        """Drop a specific collection.

        Safety constraints:
        - Rejects when dual-write is enabled (raise RuntimeError).
        - Rejects when the requested side is the active side (raise RuntimeError).
        - Accepts an explicit ``side`` ("source" or "target") only when
          dual-write is disabled and the side is non-active.
        """
        if self._dual_write_enabled:
            raise RuntimeError(
                "Cannot drop collection while dual-write is enabled"
            )
        if side is None:
            raise ValueError(
                "Must specify which side to drop ('source' or 'target')"
            )
        if side not in ("source", "target"):
            raise ValueError(
                f"Unknown side: {side!r}, must be 'source' or 'target'"
            )
        if side == self._active_side.value:
            raise RuntimeError(
                f"Cannot drop the active collection ({side})"
            )
        adapter = self._source if side == "source" else self._target
        return adapter.drop_collection()

    def create_collection(self, *args: Any, **kwargs: Any) -> bool:
        """Not supported — collection lifecycle is handled externally."""
        raise NotImplementedError(
            "DualWriteAdapter.create_collection is not supported; "
            "collections must be created before constructing this adapter"
        )

    def close(self) -> None:
        """Close both source and target adapters."""
        try:
            self._source.close()
        finally:
            self._target.close()

    def get_collection_info(self) -> Optional[Dict[str, Any]]:
        """Return collection metadata from the active side."""
        return self._active.get_collection_info()

    def get_collection(self) -> Any:
        """Return the active side's collection handle."""
        return self._active.get_collection()

    def clear(self) -> bool:
        """Clear the active side; best-effort clear on standby."""
        result = self._active.clear()
        if self._dual_write_enabled:
            self._write_to_standby("clear")
        return result

    # ------------------------------------------------------------------
    # State management (thread-safe)
    # ------------------------------------------------------------------

    def set_active(self, side: str) -> None:
        """Set the active side to ``"source"`` or ``"target"``.

        Raises ValueError for invalid values.
        """
        validated = ActiveSide(side)
        with self._lock:
            self._active_side = validated

    def set_dual_write(self, enabled: bool) -> None:
        """Enable or disable dual-write to standby."""
        with self._lock:
            self._dual_write_enabled = enabled

    def set_collection(self, collection: Any) -> None:
        """Delegate collection to both adapters."""
        with self._lock:
            self._source.set_collection(collection)
            self._target.set_collection(collection)
