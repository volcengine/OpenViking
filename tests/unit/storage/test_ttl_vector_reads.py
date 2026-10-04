# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""TTL reads work against the pre-TTL vector schema, including candidate refill."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.core.ttl import TTL_FIELD_NAMES
from openviking.pyagfs.exceptions import AGFSNetworkError
from openviking.server.identity import RequestContext, Role
from openviking.storage.collection_schemas import CollectionSchemas
from openviking.storage.expr import Eq
from openviking.storage.ovpack.index import EXPORT_VECTOR_FIELDS
from openviking.storage.vectordb.index.cuvs_index import matches_filter
from openviking.storage.vectordb_adapters.local_adapter import LocalCollectionAdapter
from openviking.storage.viking_fs import VikingFS
from openviking.storage.viking_vector_index_backend import (
    FETCH_BY_URI_OUTPUT_FIELDS,
    RETRIEVAL_OUTPUT_FIELDS,
    VikingVectorIndexBackend,
    _SingleAccountBackend,
)
from openviking_cli.session.user_id import UserIdentifier

ROOT = "viking://user/alice/memories/events"
PAST = "2000-01-01T00:00:00.000Z"
FUTURE = "2999-01-01T00:00:00.000Z"


def test_vector_schema_and_projections_need_no_ttl_columns():
    schema = CollectionSchemas.context_collection("context", 2)
    names = {item["FieldName"] for item in schema["Fields"]}
    for fields in (
        names,
        schema["ScalarIndex"],
        RETRIEVAL_OUTPUT_FIELDS,
        FETCH_BY_URI_OUTPUT_FIELDS,
        EXPORT_VECTOR_FIELDS,
    ):
        assert TTL_FIELD_NAMES.isdisjoint(fields)


def test_vector_writes_strip_lifecycle_fields_even_without_schema_metadata():
    backend = object.__new__(_SingleAccountBackend)
    backend._filter_known_fields = lambda data: data
    backend._adapter = SimpleNamespace(USE_CONTENT_FIELD=False)
    record = {
        "uri": ROOT + "/2026/09/02/event.md",
        "level": 2,
        "expires_at": FUTURE,
        "ttl_generation": "incarnation-1",
        "received_at": PAST,
        "ttl_days": 2,
    }
    assert backend._prepare_upsert_payload(record) == {"uri": record["uri"], "level": 2}
    assert record["ttl_generation"] == "incarnation-1"


@pytest.fixture
def setup(monkeypatch):
    ctx = RequestContext(user=UserIdentifier("acct", "alice"), role=Role.ROOT)
    fs = VikingFS(agfs=SimpleNamespace())
    files = {}

    async def stat(path, **kwargs):
        if path not in files:
            raise FileNotFoundError(path)
        return {"isDir": False}

    fs._async_agfs.stat = stat

    async def read(path):
        if path not in files:
            raise FileNotFoundError(path)
        return files[path]

    fs._async_agfs.read = AsyncMock(side_effect=read)
    fs.ttl_registry.account_may_have_records = AsyncMock(return_value=True)
    fs.ttl_registry.get = AsyncMock(return_value=None)
    monkeypatch.setattr("openviking.storage.viking_fs.get_viking_fs", lambda: fs)
    monkeypatch.setattr("openviking.storage.viking_vector_index_backend.ttl_enabled", lambda: False)

    def source(uri, expiry=None):
        fields = {"expires_at": expiry} if expiry else {}
        files[fs._uri_to_path(uri, ctx=ctx)] = b"body"
        files[fs._uri_to_path(uri.rsplit("/", 1)[0] + "/.meta.json", ctx=ctx)] = json.dumps(
            fields
        ).encode()

    rows, calls = [], []
    compiler = object.__new__(LocalCollectionAdapter)

    async def query(**kwargs):
        compiled = compiler._compile_filter(kwargs["filter"])
        assert "expires_at" not in json.dumps(compiled)
        assert TTL_FIELD_NAMES.isdisjoint(kwargs.get("output_fields") or [])
        calls.append(kwargs)
        selected = [
            row
            for row in rows
            if matches_filter(
                {**row, "uri": compiler._encode_uri_field_value(row["uri"])},
                compiled,
                {"uri": "path", "level": "int64", "account_id": "string"},
            )
        ]
        start = kwargs.get("offset", 0)
        return [dict(row) for row in selected[start : start + kwargs["limit"]]]

    single = SimpleNamespace(query=query, search_by_random=query, search_by_keywords=query)
    backend = object.__new__(VikingVectorIndexBackend)
    backend.acl_manager = None
    backend._get_backend_for_context = AsyncMock(return_value=single)
    return SimpleNamespace(
        ctx=ctx,
        fs=fs,
        files=files,
        source=source,
        rows=rows,
        calls=calls,
        backend=backend,
        single=single,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method",
    [
        "search_in_tenant",
        "filter_in_tenant",
        "search_by_keywords",
        "search_by_random",
    ],
)
async def test_expired_candidates_are_replaced_before_limit(setup, method):
    s = setup
    for i in range(7):
        uri = f"{ROOT}/2026/09/{i + 1:02}/{i}.md"
        s.source(uri, PAST if i < 5 else FUTURE)
        s.rows.append({"uri": uri, "level": 2, "account_id": "acct", "_score": 1 - i / 10})
    kwargs = {"ctx": s.ctx, "limit": 2}
    if method == "search_in_tenant":
        kwargs["query_vector"] = [0.1, 0.2]
    if method == "filter_in_tenant":
        kwargs["target_directories"] = [ROOT]
    result = await getattr(s.backend, method)(**kwargs)
    assert [row["uri"] for row in result] == [f"{ROOT}/2026/09/06/5.md", f"{ROOT}/2026/09/07/6.md"]
    assert len(s.calls) == 4
    assert [row["_score"] for row in result] == [0.5, pytest.approx(0.4)]


@pytest.mark.asyncio
async def test_time_decay_query_refills_expired_events_without_losing_advance(setup):
    s = setup
    expired = ROOT + "/2026/09/01/expired.md"
    live = ROOT + "/2026/09/02/live.md"
    s.source(expired, PAST)
    s.source(live, FUTURE)
    s.rows.extend(
        [
            {"uri": expired, "level": 2, "_score": 0.9},
            {"uri": live, "level": 2, "_score": 0.8},
        ]
    )
    advance = {"time_decay": {"protection": "0", "origin": "2026-09-30T00:00:00Z"}}
    result = await s.backend.query(
        query_vector=[0.1, 0.2],
        limit=1,
        ctx=s.ctx,
        include_expired=False,
        advance=advance,
    )
    assert [row["uri"] for row in result] == [live]
    assert len(s.calls) == 2
    assert all(call["advance"] == advance for call in s.calls)


@pytest.mark.asyncio
async def test_keyword_refill_preserves_bm25_parameters(setup):
    s = setup
    expired = ROOT + "/2026/09/01/expired.md"
    live = ROOT + "/2026/09/02/live.md"
    s.source(expired, PAST)
    s.source(live, FUTURE)
    s.rows.extend([{"uri": expired, "level": 2}, {"uri": live, "level": 2}])
    result = await s.backend.search_by_keywords(
        query="meeting",
        mode="bm25",
        fields=["content"],
        limit=1,
        ctx=s.ctx,
    )
    assert [row["uri"] for row in result] == [live]
    assert len(s.calls) == 2
    assert all(call["mode"] == "bm25" and call["fields"] == ["content"] for call in s.calls)


@pytest.mark.asyncio
async def test_offset_counts_live_rows_and_preserves_legacy_records(setup):
    s = setup
    for i in range(5):
        uri = f"{ROOT}/2026/09/{i + 1:02}/{i}.md"
        s.source(uri, PAST if i < 2 else None)
        s.rows.append({"uri": uri, "level": 2})
    result = await s.backend.filter(
        Eq("level", 2), limit=2, offset=1, output_fields=["uri"], ctx=s.ctx, include_expired=False
    )
    assert [row["uri"] for row in result] == [f"{ROOT}/2026/09/04/3.md", f"{ROOT}/2026/09/05/4.md"]
    assert all("expires_at" in row for row in result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "root",
    [ROOT, "viking://resources/doc", ROOT + "/2026/09/01", "viking://user/alice/sessions/s1"],
)
async def test_summaries_follow_owner_expiry_while_containers_stay_visible(setup, root):
    s = setup
    from openviking.core.ttl import ttl_object_for_uri

    target = ttl_object_for_uri(root)
    if target:
        s.files[s.fs._uri_to_path(root, ctx=s.ctx)] = b"directory"
        s.files[s.fs._uri_to_path(root + "/.meta.json", ctx=s.ctx)] = json.dumps(
            {"expires_at": PAST}
        ).encode()
    for level in (0, 1):
        s.rows.append({"uri": root, "level": level, "abstract": "summary"})
    actual = await s.backend.query(ctx=s.ctx, include_expired=False)
    assert actual == ([] if target else [{**row, "expires_at": None} for row in s.rows])


@pytest.mark.asyncio
async def test_orphan_vectors_hidden_but_raw_cleanup_query_can_find_them(setup):
    s = setup
    s.rows.append({"uri": ROOT + "/2026/09/01/removed.md", "level": 2})
    assert await s.backend.query(ctx=s.ctx, include_expired=False) == []
    assert await s.backend.query(ctx=s.ctx) == s.rows


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [OSError("storage unavailable"), AGFSNetworkError("endpoint not found")]
)
async def test_source_read_error_cannot_return_unverified_vector_content(setup, error):
    s = setup
    uri = ROOT + "/2026/09/02/event.md"
    s.source(uri, PAST)
    s.rows.append({"uri": uri, "level": 2})
    s.fs._async_agfs.read = AsyncMock(side_effect=error)
    with pytest.raises(type(error), match=str(error)):
        await s.backend.query(ctx=s.ctx, include_expired=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [OSError("storage unavailable"), AGFSNetworkError("endpoint not found")]
)
async def test_source_read_error_cannot_expose_event_name(setup, error):
    s = setup
    uri = ROOT + "/2026/09/02/event.md"
    s.source(uri, PAST)
    s.fs._async_agfs.read = AsyncMock(side_effect=error)
    with pytest.raises(type(error), match=str(error)):
        await s.fs._ttl_uri_visible(uri, s.ctx)


@pytest.mark.asyncio
async def test_partial_delete_registry_error_cannot_expose_session_subtree(setup):
    s = setup
    uri = "viking://user/alice/sessions/expired-session/messages.jsonl"
    s.fs.ttl_registry.get = AsyncMock(side_effect=OSError("registry unavailable"))
    with pytest.raises(OSError, match="registry unavailable"):
        await s.fs._ttl_uri_visible(uri, s.ctx)


@pytest.mark.asyncio
async def test_backend_ignoring_exclusion_fails_without_looping_forever(setup):
    s = setup
    row = {"uri": ROOT + "/2026/09/03/expired.md", "level": 2}
    s.source(row["uri"], PAST)
    s.single.query = AsyncMock(return_value=[row])
    with pytest.raises(RuntimeError, match="did not exclude"):
        await s.backend.query(limit=1, ctx=s.ctx, include_expired=False)
    assert s.single.query.await_count == 2


@pytest.mark.asyncio
async def test_default_off_still_checks_stored_directory_deadlines(setup):
    s = setup
    s.fs.ttl_registry.account_may_have_records.return_value = False
    uri = ROOT + "/2026/09/04/legacy.md"
    s.source(uri, PAST)
    s.rows.append({"uri": uri, "level": 2})
    assert await s.backend.query(ctx=s.ctx, include_expired=False) == []
    s.fs._async_agfs.read.assert_awaited()


@pytest.mark.asyncio
async def test_user_count_excludes_expired_while_cleanup_count_sees_residue(setup):
    s = setup
    old, live = ROOT + "/2026/09/05/old.md", ROOT + "/2026/09/06/live.md"
    s.source(old, PAST)
    s.source(live, FUTURE)
    s.single.scroll = AsyncMock(
        side_effect=[
            ([{"uri": old, "level": 2}], "1"),
            ([{"uri": live, "level": 2}], None),
        ]
    )
    s.single.count = AsyncMock(return_value=2)
    assert await s.backend.count(ctx=s.ctx, include_expired=False) == 1
    assert await s.backend.count(ctx=s.ctx) == 2
