# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Local-engine scroll must paginate via a keyset cursor, not offsets (#7468).

The local engine's scalar sorter honors ordering but ignores offset pages: any
offset returns the same first ``limit`` records, so the old numeric cursor
looped forever and fail-closed scans reported "records missing" that never
left page one. The scroll now keys each page strictly after the last record's
``updated_at``.
"""

from typing import Any, Dict, List, Optional

import pytest

from openviking.storage.expr import And, Eq, In, Or, Range
from openviking.storage.viking_vector_index_backend import _SingleAccountBackend
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


def _evaluate(expr: Any, record: Dict[str, Any]) -> bool:
    """Evaluate the filter subset the keyset scroll generates — both the raw
    expression objects the backend passes down and their compiled DSL forms."""
    if expr is None:
        return True
    if isinstance(expr, And):
        return all(_evaluate(cond, record) for cond in expr.conds if cond is not None)
    if isinstance(expr, Or):
        return any(_evaluate(cond, record) for cond in expr.conds if cond is not None)
    if isinstance(expr, Eq):
        return record.get(expr.field) == expr.value
    if isinstance(expr, In):
        return record.get(expr.field) in expr.values
    if isinstance(expr, Range):
        value = record.get(expr.field)
        if value is None:
            return False
        if expr.gt is not None and not value > expr.gt:
            return False
        if expr.gte is not None and not value >= expr.gte:
            return False
        if expr.lt is not None and not value < expr.lt:
            return False
        if expr.lte is not None and not value <= expr.lte:
            return False
        return True
    if isinstance(expr, dict):
        op = expr.get("op")
        if op == "and":
            return all(_evaluate(cond, record) for cond in expr["conds"])
        if op == "or":
            return any(_evaluate(cond, record) for cond in expr["conds"])
        if op == "must":
            return record.get(expr["field"]) in expr["conds"]
        if op == "range":
            value = record.get(expr["field"])
            if value is None:
                return False
            if expr.get("gt") is not None and not value > expr["gt"]:
                return False
            if expr.get("gte") is not None and not value >= expr["gte"]:
                return False
            if expr.get("lt") is not None and not value < expr["lt"]:
                return False
            if expr.get("lte") is not None and not value <= expr["lte"]:
                return False
            return True
    raise AssertionError(f"unexpected filter type in test: {type(expr)!r}")


class _OffsetBlindLocalAdapter:
    """Emulates the reported native behavior: ordering works, offsets don't.

    Whatever ``offset`` the caller requests, the first ``limit`` matching
    records (by updated_at asc) come back — exactly the defect behind the
    permanently-looping scroll pages in #7468.
    """

    mode = "local"
    USE_CONTENT_FIELD = False

    def __init__(self, records: List[Dict[str, Any]]):
        self._records = records

    def get_collection(self):
        return SimpleNamespaceCollection()

    def query(
        self,
        *,
        query_vector=None,
        sparse_query_vector=None,
        filter=None,
        limit: int = 10,
        offset: int = 0,
        output_fields=None,
        order_by: Optional[str] = None,
        order_desc: bool = False,
        advance=None,
    ) -> List[Dict[str, Any]]:
        assert query_vector is None
        matched = [r for r in self._records if _evaluate(filter, r)]
        matched.sort(key=lambda r: r[order_by or "updated_at"], reverse=order_desc)
        # offset is deliberately ignored — the reported engine defect
        return matched[:limit]

    def strict_count(self, *, filter=None) -> int:
        return sum(1 for r in self._records if _evaluate(filter, r))


class SimpleNamespaceCollection:
    def get_meta_data(self):
        return {"Fields": [{"FieldName": "id"}, {"FieldName": "updated_at"}]}


def make_backend(records: List[Dict[str, Any]]) -> _SingleAccountBackend:
    return _SingleAccountBackend(
        config=VectorDBBackendConfig(backend="local", name="context", dimension=4),
        bound_account_id="acc1",
        shared_adapter=_OffsetBlindLocalAdapter(records),
    )


def _seed(n: int) -> List[Dict[str, Any]]:
    return [
        {
            "id": f"rec-{i:02d}",
            "uri": f"viking://resources/doc-{i:02d}.md",
            "updated_at": f"2026-11-01T00:00:{i:02d}",
            "account_id": "acc1",
        }
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_scroll_keyset_paginates_every_record_exactly_once():
    """Seven records, page size 3: three pages (3/3/1) cover all records in
    updated_at order — the offset-blind engine can no longer repeat page one."""
    records = _seed(7)
    backend = make_backend(records)

    seen: List[str] = []
    cursor: Optional[str] = None
    cursors: List[Optional[str]] = []
    for _ in range(10):
        page, next_cursor = await backend.scroll(limit=3, cursor=cursor)
        seen.extend(r["id"] for r in page)
        cursors.append(next_cursor)
        if next_cursor is None:
            break
        cursor = next_cursor

    assert seen == [f"rec-{i:02d}" for i in range(7)]
    assert cursors[-1] is None
    assert len(set(c for c in cursors if c is not None)) == len(
        [c for c in cursors if c is not None]
    )


@pytest.mark.asyncio
async def test_scroll_keyset_combines_with_caller_filter():
    """The keyset range is AND-ed into the caller's filter, not replacing it."""
    records = _seed(8)
    backend = make_backend(records)

    seen: List[str] = []
    cursor: Optional[str] = None
    for _ in range(10):
        page, next_cursor = await backend.scroll(
            filter=Eq("uri", "viking://resources/doc-03.md"),
            limit=3,
            cursor=cursor,
        )
        seen.extend(r["id"] for r in page)
        if next_cursor is None:
            break
        cursor = next_cursor

    assert seen == ["rec-03"]


@pytest.mark.asyncio
async def test_scroll_strips_internally_added_sort_field():
    """When the caller's output_fields omit updated_at, the keyset adds it for
    the engine round-trip and strips it from the returned records."""
    records = _seed(4)
    backend = make_backend(records)

    page, next_cursor = await backend.scroll(limit=2, output_fields=["id", "uri"])

    assert all("updated_at" not in r for r in page)
    assert next_cursor is not None  # cursor still computed from the hidden field
    assert page[0]["id"] == "rec-00"


@pytest.mark.asyncio
async def test_scroll_keyset_drives_strict_scan_to_expected_count():
    """The failing production shape (#7468): a URI whose cover-write leaves two
    records must scan both — the old offset cursor stopped after one."""
    from openviking.storage.expr import In

    records = [
        {
            "id": "rec-old",
            "uri": "viking://resources/README.md",
            "updated_at": "2026-11-01T00:00:00",
            "account_id": "acc1",
        },
        {
            "id": "rec-new",
            "uri": "viking://resources/README.md",
            "updated_at": "2026-11-01T00:00:05",
            "account_id": "acc1",
        },
        {
            "id": "rec-other",
            "uri": "viking://resources/other.md",
            "updated_at": "2026-11-01T00:00:02",
            "account_id": "acc1",
        },
    ]
    backend = make_backend(records)

    seen: List[str] = []
    cursor: Optional[str] = None
    for _ in range(10):
        page, next_cursor = await backend.scroll(
            filter=In("uri", ["viking://resources/README.md"]),
            limit=1,
            cursor=cursor,
        )
        seen.extend(r["id"] for r in page)
        if next_cursor is None:
            break
        cursor = next_cursor

    assert sorted(seen) == ["rec-new", "rec-old"]


@pytest.mark.asyncio
async def test_scroll_tie_block_spanning_a_page_boundary_emits_every_member():
    """A timestamp tie spanning the page boundary must not strand members.

    The directory producer enqueues L0 and L1 with one resolved ``updated_at``
    (``vectorize_directory_meta``), so ties are real existing records — and a
    strict-gt cursor at the tie's timestamp drops everyone still inside it
    (review finding 2 on #7471): three records share one timestamp, page size
    two, the old walk returned page 1 and then jumped past the tie.
    """
    records = [
        {"id": "w-0", "uri": "viking://resources/f0.md", "updated_at": "2026-11-01T00:00:00", "account_id": "acc1"},
        {"id": "w-1", "uri": "viking://resources/f1.md", "updated_at": "2026-11-01T00:02:00", "account_id": "acc1"},
        {"id": "w-2", "uri": "viking://resources/f2.md", "updated_at": "2026-11-01T00:02:00", "account_id": "acc1"},
        {"id": "w-3", "uri": "viking://resources/f3.md", "updated_at": "2026-11-01T00:02:00", "account_id": "acc1"},
        {"id": "w-4", "uri": "viking://resources/f4.md", "updated_at": "2026-11-01T00:04:00", "account_id": "acc1"},
    ]
    backend = make_backend(records)

    seen: List[str] = []
    cursor: Optional[str] = None
    for _ in range(10):
        page, next_cursor = await backend.scroll(
            filter=In("uri", [r["uri"] for r in records]),
            limit=2,
            cursor=cursor,
        )
        seen.extend(r["id"] for r in page)
        if next_cursor is None:
            break
        cursor = next_cursor

    assert sorted(seen) == [f"w-{i}" for i in range(5)]
    assert len(seen) == len(set(seen)), "no record emitted twice"


@pytest.mark.asyncio
async def test_scroll_directory_producer_tie_emits_both_l0_l1():
    """The exact production shape: L0/L1 with one shared timestamp, page size
    1. Both records must be traversed and the scan must complete."""
    ts = "2026-11-01T00:00:00"
    records = [
        {"id": "ctx-1", "uri": "viking://resources/dir/.abstract.md", "updated_at": ts, "account_id": "acc1"},
        {"id": "ctx-2", "uri": "viking://resources/dir/.overview.md", "updated_at": ts, "account_id": "acc1"},
    ]
    backend = make_backend(records)

    seen: List[str] = []
    cursor: Optional[str] = None
    for _ in range(10):
        page, next_cursor = await backend.scroll(
            filter=In("uri", ["viking://resources/dir/.abstract.md", "viking://resources/dir/.overview.md"]),
            limit=1,
            cursor=cursor,
        )
        seen.extend(r["id"] for r in page)
        if next_cursor is None:
            break
        cursor = next_cursor

    assert sorted(seen) == ["ctx-1", "ctx-2"]


@pytest.mark.asyncio
async def test_scroll_tie_larger_than_page_limit_is_fully_emitted():
    """A tie bigger than the page: the completion read is capped at ``limit``
    first, then re-read with the true count, so the block is complete before
    the cursor lands on its timestamp."""
    records = _seed(5)
    tie_ts = "2026-11-01T00:09:00"
    for i in range(4):
        records.append({"id": f"tie-{i}", "uri": f"viking://resources/t{i}.md", "updated_at": tie_ts, "account_id": "acc1"})
    backend = make_backend(records)

    seen: List[str] = []
    cursor: Optional[str] = None
    for _ in range(20):
        page, next_cursor = await backend.scroll(
            filter=In("uri", [r["uri"] for r in records]),
            limit=2,
            cursor=cursor,
        )
        seen.extend(r["id"] for r in page)
        if next_cursor is None:
            break
        cursor = next_cursor

    assert sorted(seen) == sorted([r["id"] for r in records])
    assert len(seen) == len(set(seen))
