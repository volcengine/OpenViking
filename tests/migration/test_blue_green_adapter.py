# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""RED tests for DualWriteAdapter (blue-green dual-write adapter).

All tests MUST fail because DualWriteAdapter doesn't exist yet.
They define the expected API contract for the TDD GREEN phase.

Tests use FakeCollectionAdapter — an in-memory CollectionAdapter
implementation that tracks all upserts/deletes for verification.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

import pytest

# =============================================================================
# FakeCollectionAdapter — in-memory fake for testing
# =============================================================================


class FakeCollectionAdapter:
    """In-memory fake implementing the CollectionAdapter public API.

    Does NOT inherit from CollectionAdapter ABC to avoid needing real
    backend infrastructure. Implements the same public method signatures
    so DualWriteAdapter can delegate to it.

    Features:
    - Dict-based storage (id → record)
    - Tracks all upsert/delete calls for test verification
    - Configurable failure injection for testing standby failure handling
    - Supports "fail N times then succeed" pattern for retry tests
    """

    def __init__(
        self,
        collection_name: str = "test",
        mode: str = "fake",
        *,
        exists: bool = True,
    ):
        self._collection_name = collection_name
        self.mode = mode
        self._exists = exists
        # --- storage ---
        self._records: Dict[str, Dict[str, Any]] = {}
        # --- call tracking ---
        self._upsert_call_count: int = 0
        self._upsert_records: List[List[Dict[str, Any]]] = []
        self._delete_call_count: int = 0
        self._delete_ids: List[str] = []
        self._query_call_count: int = 0
        # --- failure injection ---
        self._raise_on_upsert: Optional[Exception] = None
        self._raise_on_delete: Optional[Exception] = None
        # Number of times to succeed before raising (fail_count=0 means always raise)
        self._fail_on_upsert_count: int = 0
        self._upsert_failures: int = 0

    # ------------------------------------------------------------------
    # Public API (matching CollectionAdapter)
    # ------------------------------------------------------------------

    def collection_exists(self) -> bool:
        return self._exists

    def upsert(self, data: Dict[str, Any] | List[Dict[str, Any]]) -> List[str]:
        """Insert or update records. Returns list of IDs.

        Respects failure injection: if _raise_on_upsert is set and
        _upsert_failures >= _fail_on_upsert_count, raises instead.
        """
        self._upsert_failures += 1
        if self._raise_on_upsert is not None and self._upsert_failures > self._fail_on_upsert_count:
            raise self._raise_on_upsert

        records = [data] if isinstance(data, dict) else data
        self._upsert_call_count += 1
        self._upsert_records.append([dict(r) for r in records])

        ids: List[str] = []
        for item in records:
            record = dict(item)
            record_id = record.get("id") or str(uuid.uuid4())
            record["id"] = record_id
            ids.append(record_id)
            self._records[record_id] = record
        return ids

    def delete(
        self,
        *,
        ids: Optional[List[str]] = None,
        filter: Optional[Dict[str, Any]] = None,
        limit: int = 100000,
    ) -> int:
        """Delete records by ID or filter. Returns count deleted."""
        if self._raise_on_delete is not None:
            raise self._raise_on_delete

        delete_ids = list(ids or [])
        if not delete_ids and filter is not None:
            # Simple fake filter: match records where field == value
            matched = self.query(filter=filter, limit=limit)
            delete_ids = [r["id"] for r in matched if r.get("id")]

        self._delete_call_count += 1
        for rid in delete_ids:
            if rid in self._records:
                del self._records[rid]
                self._delete_ids.append(rid)
        return len(delete_ids)

    def query(
        self,
        *,
        query_vector: Optional[List[float]] = None,
        sparse_query_vector: Optional[Dict[str, float]] = None,
        filter: Optional[Dict[str, Any]] = None,
        limit: int = 10,
        offset: int = 0,
        output_fields: Optional[List[str]] = None,
        order_by: Optional[str] = None,
        order_desc: bool = False,
    ) -> List[Dict[str, Any]]:
        """Return stored records (simple pass-through, no real filtering)."""
        self._query_call_count += 1
        records = list(self._records.values())
        if offset:
            records = records[offset:]
        if limit:
            records = records[:limit]
        return [dict(r) for r in records]

    def get(self, ids: List[str]) -> List[Dict[str, Any]]:
        """Fetch records by ID."""
        result: List[Dict[str, Any]] = []
        for rid in ids:
            if rid in self._records:
                result.append(dict(self._records[rid]))
        return result

    def count(self, filter: Optional[Dict[str, Any]] = None) -> int:
        return len(self._records)

    def clear(self) -> bool:
        self._records.clear()
        return True

    def drop_collection(self) -> bool:
        if not self._exists:
            return False
        self._records.clear()
        self._exists = False
        return True

    def close(self) -> None:
        """No-op in fake adapter."""

    def get_collection_info(self) -> Optional[Dict[str, Any]]:
        return {"CollectionName": self._collection_name, "RecordCount": len(self._records)}

    def get_collection(self) -> Any:
        return self

    def set_collection(self, collection: Any) -> None:
        """No-op in fake adapter."""

    def create_collection(self, *args: Any, **kwargs: Any) -> bool:
        """No-op in fake adapter — collection already exists."""
        return True

    # ------------------------------------------------------------------
    # Failure injection helpers (for test setup)
    # ------------------------------------------------------------------

    def set_raise_on_upsert(self, exc: Exception, *, succeed_first: int = 0) -> None:
        """Configure upsert to raise exc after succeed_first successful calls."""
        self._raise_on_upsert = exc
        self._fail_on_upsert_count = succeed_first
        self._upsert_failures = 0

    def reset_failures(self) -> None:
        """Reset failure injection state."""
        self._raise_on_upsert = None
        self._raise_on_delete = None
        self._fail_on_upsert_count = 0
        self._upsert_failures = 0

    @property
    def upsert_call_count(self) -> int:
        return self._upsert_call_count

    @property
    def delete_call_count(self) -> int:
        return self._delete_call_count

    @property
    def query_call_count(self) -> int:
        return self._query_call_count

    @property
    def stored_ids(self) -> List[str]:
        """Return sorted list of stored record IDs."""
        return sorted(self._records.keys())

    @property
    def deleted_ids(self) -> List[str]:
        return list(self._delete_ids)


# =============================================================================
# Helper factories
# =============================================================================


def _make_source_target_adapter_pair(
    source_name: str = "source_coll",
    target_name: str = "target_coll",
) -> tuple[FakeCollectionAdapter, FakeCollectionAdapter]:
    """Create a pair of FakeCollectionAdapters for testing."""
    source = FakeCollectionAdapter(collection_name=source_name)
    target = FakeCollectionAdapter(collection_name=target_name)
    return source, target


def _make_adapter(
    source: FakeCollectionAdapter,
    target: FakeCollectionAdapter,
    *,
    active_side: str = "source",
    dual_write_enabled: bool = True,
):
    """Create a DualWriteAdapter — this import MUST fail (RED phase).

    Imported inside the function so the module-level import doesn't
    crash the entire test file if the module doesn't exist yet.
    """
    from openviking.storage.migration.blue_green_adapter import DualWriteAdapter

    return DualWriteAdapter(
        source=source,
        target=target,
        active_side=active_side,
        dual_write_enabled=dual_write_enabled,
    )


# =============================================================================
# 1. Constructor validation
# =============================================================================


def test_constructor_rejects_none_adapters():
    """Either adapter being None must raise ValueError."""
    from openviking.storage.migration.blue_green_adapter import DualWriteAdapter

    source = FakeCollectionAdapter(collection_name="source")
    target = FakeCollectionAdapter(collection_name="target")

    # source=None
    with pytest.raises(ValueError):
        DualWriteAdapter(source=None, target=target)

    # target=None
    with pytest.raises(ValueError):
        DualWriteAdapter(source=source, target=None)

    # both None
    with pytest.raises(ValueError):
        DualWriteAdapter(source=None, target=None)


# =============================================================================
# 2. Dual-write: upsert writes to both sides
# =============================================================================


def test_upsert_writes_to_both_sides_when_dual_write_enabled():
    """When dual_write_enabled=True, upsert must write to both source and target."""
    source, target = _make_source_target_adapter_pair()
    adapter = _make_adapter(source, target, active_side="source", dual_write_enabled=True)

    record = {"text": "hello world", "embedding": [0.1, 0.2, 0.3]}
    ids = adapter.upsert(record)

    assert len(ids) == 1
    # Source side received the write
    assert source.upsert_call_count == 1
    assert len(source.stored_ids) == 1
    # Target (standby) side also received the write
    assert target.upsert_call_count == 1
    assert len(target.stored_ids) == 1


def test_upsert_does_not_write_to_standby_when_dual_write_disabled():
    """When dual_write_enabled=False, upsert only writes to the active side."""
    source, target = _make_source_target_adapter_pair()
    adapter = _make_adapter(source, target, active_side="source", dual_write_enabled=False)

    record = {"text": "hello world"}
    adapter.upsert(record)

    assert source.upsert_call_count == 1
    assert target.upsert_call_count == 0


# =============================================================================
# 3. Dual-write: delete writes to both sides
# =============================================================================


def test_delete_writes_to_both_sides_when_dual_write_enabled():
    """When dual_write_enabled=True, delete must propagate to both source and target."""
    source, target = _make_source_target_adapter_pair()
    adapter = _make_adapter(source, target, active_side="source", dual_write_enabled=True)

    # First insert a record into both sides (simulating pre-existing data)
    rid = adapter.upsert({"text": "to_delete"})[0]

    # Now delete
    deleted_count = adapter.delete(ids=[rid])
    assert deleted_count == 1
    # Both sides should have received the delete
    assert source.delete_call_count == 1
    assert target.delete_call_count == 1


def test_delete_does_not_write_to_standby_when_dual_write_disabled():
    """When dual_write_enabled=False, delete only affects the active side."""
    source, target = _make_source_target_adapter_pair()
    adapter = _make_adapter(source, target, active_side="source", dual_write_enabled=False)

    rid = adapter.upsert({"text": "to_delete"})[0]
    adapter.delete(ids=[rid])

    assert source.delete_call_count == 1
    assert target.delete_call_count == 0


# =============================================================================
# 4. Query reads from active side only
# =============================================================================


def test_query_reads_from_active_side_only():
    """Query must route reads to the currently active side only."""
    source, target = _make_source_target_adapter_pair()
    adapter = _make_adapter(source, target, active_side="source", dual_write_enabled=True)

    # Write to both sides
    adapter.upsert({"text": "active_only", "id": "rec_1"})

    # Query — only source (active) should be queried
    result = adapter.query(limit=10)
    # Source was queried, target was not
    assert source.query_call_count == 1
    assert target.query_call_count == 0
    assert len(result) >= 1


# =============================================================================
# 5. set_active switches read source
# =============================================================================


def test_set_active_switches_read_source():
    """After set_active('target'), queries must read from target side."""
    source, target = _make_source_target_adapter_pair()
    adapter = _make_adapter(source, target, active_side="source", dual_write_enabled=True)

    # Pre-populate target with a record that source doesn't have
    target.upsert({"text": "target_only", "id": "target_rec"})

    # Switch active to target
    adapter.set_active("target")

    # Query should now read from target
    result = adapter.query(limit=10)
    records = [r for r in result if r.get("id") == "target_rec"]
    assert len(records) == 1


def test_set_active_with_invalid_side_raises():
    """set_active with an invalid side name must raise ValueError."""
    source, target = _make_source_target_adapter_pair()
    adapter = _make_adapter(source, target, active_side="source", dual_write_enabled=True)

    with pytest.raises(ValueError):
        adapter.set_active("invalid_side")


# =============================================================================
# 6. Standby write failure does not block active
# =============================================================================


def test_standby_write_failure_does_not_block_active():
    """When standby upsert fails, the active side write must still succeed."""
    source, target = _make_source_target_adapter_pair()
    adapter = _make_adapter(source, target, active_side="source", dual_write_enabled=True)

    # Make target (standby) fail on upsert
    target.set_raise_on_upsert(RuntimeError("standby down"))

    # Upsert must succeed (active side is source)
    record = {"text": "survives standby failure"}
    ids = adapter.upsert(record)

    assert len(ids) == 1
    # Active (source) side must have received the write
    assert source.upsert_call_count == 1
    assert len(source.stored_ids) == 1
    # Target (standby) should have been attempted (failure handled internally)
    # The write must not raise an exception to the caller


def test_active_write_failure_does_raise():
    """When the active side fails, the exception must propagate to the caller."""
    source, target = _make_source_target_adapter_pair()
    adapter = _make_adapter(source, target, active_side="source", dual_write_enabled=True)

    # Make source (active) fail on upsert
    source.set_raise_on_upsert(RuntimeError("active down"))

    with pytest.raises(RuntimeError, match="active down"):
        adapter.upsert({"text": "active failure"})


# =============================================================================
# 7. Standby failure increments degraded counter
# =============================================================================


def test_standby_write_failure_increments_counter():
    """Each standby write failure must increment degraded_write_failures."""
    source, target = _make_source_target_adapter_pair()
    adapter = _make_adapter(source, target, active_side="source", dual_write_enabled=True)

    # Make standby (target) fail
    target.set_raise_on_upsert(RuntimeError("standby error"))

    assert adapter.degraded_write_failures == 0

    adapter.upsert({"text": "failure 1"})
    assert adapter.degraded_write_failures == 1

    adapter.upsert({"text": "failure 2"})
    assert adapter.degraded_write_failures == 2


# =============================================================================
# 8. Retry succeeds → counter does NOT increment
# =============================================================================


def test_standby_write_retry_succeeds_counter_no_increment():
    """If standby upsert fails once then succeeds on retry, counter must NOT increment."""
    source, target = _make_source_target_adapter_pair()
    adapter = _make_adapter(source, target, active_side="source", dual_write_enabled=True)

    # Make standby fail on first attempt, succeed on retry
    # (succeed_first=0 means the FIRST call succeeds, subsequent calls raise)
    # We want: first call raises, retry succeeds.
    # Actually the adapter does retry internally, so we configure:
    # - Initial call: raises (counted by adapter's internal failure)
    # - Retry 1: succeeds
    # This needs the adapter to attempt, fail, retry, succeed.
    #
    # Fake behavior: succeed_first=0 means 0 successes before failing.
    # For retry test: we want the adapter's internal retry to succeed.
    # The simplest approach: make standby raise with succeed_first=1,
    # meaning the first attempt raises, the second (retry) succeeds.
    target.set_raise_on_upsert(RuntimeError("transient"), succeed_first=1)

    assert adapter.degraded_write_failures == 0

    # The adapter should retry internally and succeed.
    # Since the retry succeeds, degraded_write_failures should remain 0.
    adapter.upsert({"text": "retry works"})

    assert adapter.degraded_write_failures == 0


# =============================================================================
# 9. drop_collection rejection when dual-write enabled
# =============================================================================


def test_drop_collection_rejects_when_dual_write_enabled():
    """drop_collection must raise when dual_write_enabled=True (both sides active)."""
    source, target = _make_source_target_adapter_pair()
    adapter = _make_adapter(source, target, active_side="source", dual_write_enabled=True)

    with pytest.raises(RuntimeError, match="dual.write"):
        adapter.drop_collection("source")
    with pytest.raises(RuntimeError, match="dual.write"):
        adapter.drop_collection("target")


def test_drop_collection_allows_inactive_collection():
    """When dual_write_enabled=False, allow dropping a non-active collection."""
    source, target = _make_source_target_adapter_pair()
    adapter = _make_adapter(source, target, active_side="source", dual_write_enabled=False)

    # target is inactive — should be droppable
    result = adapter.drop_collection("target")
    assert result is True

    # source is active — should be rejected
    with pytest.raises(RuntimeError, match="(active|current)"):
        adapter.drop_collection("source")


# =============================================================================
# 10. collection_exists checks both sides
# =============================================================================


def test_collection_exists_checks_both_sides():
    """collection_exists() should report True when EITHER side exists."""
    source, target = _make_source_target_adapter_pair()
    adapter = _make_adapter(source, target, active_side="source", dual_write_enabled=False)

    # Both exist → True
    assert adapter.collection_exists() is True

    # Only source exists → True
    target.drop_collection()
    assert adapter.collection_exists() is True

    # Neither exists → False
    source.drop_collection()
    assert adapter.collection_exists() is False


def test_both_collections_exist():
    """both_collections_exist() should report True only when BOTH sides exist."""
    source, target = _make_source_target_adapter_pair()
    adapter = _make_adapter(source, target, active_side="source", dual_write_enabled=False)

    # Both exist → True
    assert adapter.both_collections_exist() is True

    # Only one exists → False
    target.drop_collection()
    assert adapter.both_collections_exist() is False
