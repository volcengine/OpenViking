# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import threading
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.acl import AclManager
from openviking.storage.collection_schemas import CollectionSchemas
from openviking.storage.expr import And, Eq, In, Or, PathScope, RawDSL
from openviking.storage.viking_vector_index_backend import (
    VikingVectorIndexBackend,
    _SingleAccountBackend,
)
from openviking.utils.time_decay import MAX_TIME_DECAY_CANDIDATES
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


def _ctx(*, role: Role = Role.USER, actor_peer_id: str | None = None) -> RequestContext:
    return RequestContext(
        user=UserIdentifier("acct", "alice"),
        role=role,
        actor_peer_id=actor_peer_id,
    )


def _build(
    ctx: RequestContext,
    targets: list[str] | None,
    *,
    context_type: str | None = "resource",
    extra_filter=None,
    level: list[int] | None = None,
    acl_enabled: bool = False,
):
    backend = object.__new__(VikingVectorIndexBackend)
    backend.acl_manager = None
    return backend._build_scope_filter(
        ctx=ctx,
        context_type=context_type,
        target_directories=targets,
        extra_filter=extra_filter,
        level=level,
        acl_enabled=acl_enabled,
    )


def _tenant_filter(ctx: RequestContext, *, acl_enabled: bool = False):
    backend = object.__new__(VikingVectorIndexBackend)
    backend.acl_manager = None
    return backend._tenant_filter(ctx, acl_enabled=acl_enabled)


class _AclConfigReader:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    async def get_account(self, account_id: str, field: str):
        del account_id, field
        return SimpleNamespace(enabled=self.enabled)


class _FailingAsyncAdapter:
    async def call(self, method_name, **kwargs):
        raise RuntimeError(f"{method_name} failed")


class _RecordingAsyncAdapter:
    def __init__(self):
        self.calls = []

    async def call(self, method_name, **kwargs):
        self.calls.append((method_name, kwargs))
        return []


def _backend_with_type(backend_type: str) -> VikingVectorIndexBackend:
    backend = object.__new__(VikingVectorIndexBackend)
    backend.acl_manager = None
    backend._backend_type = backend_type
    backend._get_backend_for_context = AsyncMock(
        return_value=SimpleNamespace(_mode=backend_type)
    )
    return backend


def _contains_expr(expr, expected) -> bool:
    if expr == expected:
        return True
    if isinstance(expr, (And, Or)):
        return any(_contains_expr(cond, expected) for cond in expr.conds)
    return False


@pytest.mark.asyncio
async def test_cloud_event_scope_uses_cloud_decay_and_semantic_queries():
    backend = _backend_with_type("vikingdb")
    calls = []

    async def fake_search(**kwargs):
        calls.append(kwargs)
        return []

    backend.search = fake_search
    await backend.search_in_tenant(
        ctx=_ctx(actor_peer_id="assistant"),
        query_vector=[1.0],
        context_type="memory",
        target_directories=["viking://user/alice/peers/assistant/memories/events"],
        level=[2],
        limit=10,
        events_time_decay_protection="0",
        request_now=datetime(2026, 1, 8, tzinfo=timezone.utc),
    )

    assert len(calls) == 2
    cloud_call = next(call for call in calls if call.get("advance"))
    assert cloud_call["limit"] == 10
    assert cloud_call["return_detail_info"] is True
    assert cloud_call["advance"]["post_process_input_limit"] == MAX_TIME_DECAY_CANDIDATES
    assert cloud_call["advance"]["post_process_ops"][0]["fusion_by"] == "multiply"
    semantic_call = next(call for call in calls if call.get("advance") is None)
    assert semantic_call["limit"] == 10
    assert _contains_expr(
        semantic_call["filter"],
        RawDSL({"op": "must_not", "field": "search_tags", "conds": ["memory_type=events"]}),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("backend_type", ["vikingdb", "volcengine"])
async def test_cloud_decay_keeps_operator_scores_without_local_fusion(backend_type):
    backend = _backend_with_type(backend_type)
    cloud_result = {
        "uri": "viking://user/alice/peers/assistant/memories/events/past.md",
        "context_type": "memory",
        "level": 2,
        "search_tags": ["memory_type=events"],
        "updated_at": "2026-01-01T00:00:00Z",
        "_score": 0.4,
        "_origin_score": 0.8,
        "_time_score": 0.5,
    }

    async def fake_search(**kwargs):
        if kwargs.get("advance"):
            return [dict(cloud_result)]
        return []

    backend.search = fake_search
    with patch(
        "openviking.storage.viking_vector_index_backend.build_time_decay_fusion_spec",
        side_effect=AssertionError("Cloud results must not be fused in Python"),
    ):
        results = await backend.search_in_tenant(
            ctx=_ctx(),
            query_vector=[1.0],
            context_type="memory",
            limit=2,
            events_time_decay_protection="0",
            request_now=datetime(2026, 1, 8, tzinfo=timezone.utc),
        )

    assert results == [cloud_result]


@pytest.mark.asyncio
@pytest.mark.parametrize("backend_type", ["local", "vikingdb", "volcengine"])
async def test_event_directory_does_not_override_memory_type_tag(backend_type):
    backend = _backend_with_type(backend_type)
    result = {
        "uri": "viking://user/alice/memories/events/old.md",
        "context_type": "memory",
        "level": 2,
        "search_tags": ["memory_type=preferences"],
        "updated_at": "2026-01-01T00:00:00Z",
        "_score": 0.8,
    }

    async def fake_search(**kwargs):
        if _contains_expr(kwargs["filter"], Eq("search_tags", "memory_type=events")):
            return []
        return [dict(result)]

    backend.search = fake_search
    results = await backend.search_in_tenant(
        ctx=_ctx(),
        query_vector=[1.0],
        context_type="memory",
        target_directories=["viking://user/alice/memories/events"],
        level=[2],
        limit=1,
        events_time_decay_protection="0",
        request_now=datetime(2026, 1, 8, tzinfo=timezone.utc),
    )

    assert results == [result]


@pytest.mark.asyncio
async def test_local_decay_considers_candidates_beyond_the_final_window():
    backend = _backend_with_type("local")
    calls = []
    candidates = [
        {
            "uri": f"viking://user/alice/memories/events/old-{index}.md",
            "context_type": "memory",
            "level": 2,
            "updated_at": "2025-01-01T00:00:00Z",
            "_score": score,
        }
        for index, score in enumerate((0.99, 0.98, 0.97), start=1)
    ]
    candidates.append(
        {
            "uri": "viking://user/alice/memories/events/fresh.md",
            "context_type": "memory",
            "level": 2,
            "updated_at": "2026-01-08T00:00:00Z",
            "_score": 0.96,
        }
    )

    async def fake_search(**kwargs):
        calls.append(kwargs)
        if not _contains_expr(kwargs["filter"], Eq("search_tags", "memory_type=events")):
            return []
        return [dict(candidate) for candidate in candidates[: kwargs["limit"]]]

    backend.search = fake_search
    results = await backend.search_in_tenant(
        ctx=_ctx(),
        query_vector=[1.0],
        context_type="memory",
        target_directories=["viking://user/alice/memories/events"],
        level=[2],
        limit=1,
        events_time_decay_protection="0",
        request_now=datetime(2026, 1, 8, tzinfo=timezone.utc),
    )

    assert max(call["limit"] for call in calls) == MAX_TIME_DECAY_CANDIDATES
    assert results[0]["uri"] == "viking://user/alice/memories/events/fresh.md"


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [0, 1])
async def test_cloud_mixed_scope_splits_event_and_non_event_queries(offset):
    backend = _backend_with_type("vikingdb")
    calls = []

    async def fake_search(**kwargs):
        calls.append(kwargs)
        if kwargs.get("advance"):
            return [
                {
                    "uri": "viking://user/alice/memories/events/new",
                    "context_type": "memory",
                    "level": 2,
                    "_score": 0.7,
                }
            ]
        return [{"uri": "viking://user/alice/memories/preferences/p", "_score": 0.8}]

    backend.search = fake_search
    results = await backend.search_in_tenant(
        ctx=_ctx(),
        query_vector=[1.0],
        context_type="memory",
        target_directories=["viking://user/alice/memories"],
        limit=1,
        offset=offset,
        events_time_decay_protection="0",
        request_now=datetime(2026, 1, 8, tzinfo=timezone.utc),
    )

    assert len(calls) == 2
    assert sum(call.get("return_detail_info", False) for call in calls) == 1
    assert all(call["limit"] == 1 + offset and call["offset"] == 0 for call in calls)
    assert [item["_score"] for item in results] == [[0.8], [0.7]][offset]


@pytest.mark.asyncio
async def test_cloud_mixed_rerank_prefetch_keeps_one_origin_score_window():
    backend = _backend_with_type("vikingdb")

    async def fake_search(**kwargs):
        if _contains_expr(kwargs["filter"], Eq("search_tags", "memory_type=events")):
            return [
                {
                    "uri": "viking://user/alice/memories/events/a",
                    "level": 2,
                    "updated_at": "2026-01-08T00:00:00Z",
                    "_score": 0.4,
                },
                {
                    "uri": "viking://user/alice/memories/events/b",
                    "level": 2,
                    "updated_at": "2026-01-01T00:00:00Z",
                    "_score": 0.3,
                },
                {
                    "uri": "viking://user/alice/memories/events/c",
                    "level": 2,
                    "updated_at": "2025-12-25T00:00:00Z",
                    "_score": 0.2,
                },
            ]
        return [
            {"uri": "viking://user/alice/memories/preferences/a", "_score": 0.8},
            {"uri": "viking://user/alice/memories/preferences/b", "_score": 0.7},
            {"uri": "viking://user/alice/memories/preferences/c", "_score": 0.1},
        ]

    backend.search = fake_search
    results = await backend.search_in_tenant(
        ctx=_ctx(),
        query_vector=[1.0],
        context_type="memory",
        target_directories=["viking://user/alice/memories"],
        limit=1,
        events_time_decay_protection="0",
        for_rerank=True,
        request_now=datetime(2026, 1, 8, tzinfo=timezone.utc),
    )

    assert len(results) == 3
    assert [item["_score"] for item in results] == [0.8, 0.7, 0.4]
    assert [item["_time_score"] for item in results if "_time_score" in item] == [1.0]


@pytest.mark.asyncio
async def test_cloud_default_user_scope_splits_tagged_events_without_a_peer():
    backend = _backend_with_type("vikingdb")
    calls = []

    async def fake_search(**kwargs):
        calls.append(kwargs)
        return []

    backend.search = fake_search
    await backend.search_in_tenant(
        ctx=_ctx(),
        query_vector=[1.0],
        context_type="memory",
        limit=10,
        events_time_decay_protection="0",
        request_now=datetime(2026, 1, 8, tzinfo=timezone.utc),
    )

    assert len(calls) == 2
    cloud_call = next(call for call in calls if call.get("advance"))
    assert cloud_call["advance"]["post_process_input_limit"] == MAX_TIME_DECAY_CANDIDATES
    assert _contains_expr(cloud_call["filter"], Eq("search_tags", "memory_type=events"))
    semantic_call = next(call for call in calls if call.get("advance") is None)
    assert semantic_call["limit"] == 10
    assert _contains_expr(
        semantic_call["filter"],
        RawDSL({"op": "must_not", "field": "search_tags", "conds": ["memory_type=events"]}),
    )
    assert semantic_call["return_detail_info"] is False


def _single_account_backend(async_adapter, account_id: str | None):
    backend = object.__new__(_SingleAccountBackend)
    backend._bound_account_id = account_id
    backend._operation_condition = threading.Condition()
    backend._operations = {}
    backend._retired = False
    backend._async_adapter = async_adapter
    return backend


@pytest.mark.asyncio
async def test_search_by_random_passes_runtime_acl_state_to_tenant_filter():
    ctx = _ctx()
    adapter = SimpleNamespace(search_by_random=AsyncMock(return_value=[]))
    acl_reader = _AclConfigReader(False)
    backend = object.__new__(VikingVectorIndexBackend)
    backend.acl_manager = AclManager(backend, acl_reader)
    backend._get_backend_for_context = AsyncMock(return_value=adapter)

    assert await backend.search_by_random(ctx=ctx) == []
    adapter.search_by_random.assert_awaited_once()
    assert adapter.search_by_random.await_args.kwargs["filter"] == _tenant_filter(ctx)


def test_descendant_target_elides_only_visible_root_path_filter():
    ctx = _ctx()
    target = "viking://resources/wiki/physics"

    result = _build(
        ctx,
        [target],
        extra_filter=Eq("status", "ready"),
        level=[2],
    )

    assert result == And(
        [
            Eq("context_type", "resource"),
            Eq("account_id", "acct"),
            Or([PathScope("uri", target, depth=-1)]),
            Eq("status", "ready"),
            In("level", [2]),
        ]
    )


def test_equal_visible_root_elides_only_visible_root_path_filter():
    ctx = _ctx()

    result = _build(ctx, ["viking://resources"])

    assert result == And(
        [
            Eq("context_type", "resource"),
            Eq("account_id", "acct"),
            Or([PathScope("uri", "viking://resources", depth=-1)]),
        ]
    )


def test_all_targets_may_be_under_different_visible_roots():
    ctx = _ctx()
    targets = [
        "viking://resources/wiki/physics",
        "viking://user/alice/resources/private-notes",
        "viking://agent/tools/search",
    ]

    result = _build(ctx, targets)

    assert result == And(
        [
            Eq("context_type", "resource"),
            Eq("account_id", "acct"),
            Or(
                [
                    PathScope("uri", "viking://resources/wiki/physics", depth=-1),
                    PathScope("uri", "viking://user/alice/resources/private-notes", depth=-1),
                    PathScope("uri", "viking://agent/tools/search", depth=-1),
                ]
            ),
        ]
    )


def test_mixed_visible_and_outside_targets_keep_original_tenant_filter():
    ctx = _ctx()
    targets = ["viking://resources/wiki", "viking://upload/staged"]

    result = _build(ctx, targets)

    assert result == And(
        [
            Eq("context_type", "resource"),
            _tenant_filter(ctx),
            Or([PathScope("uri", target, depth=-1) for target in targets]),
        ]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_mode", [{}, {"acl_mode": None}, {"acl_mode": "none"}])
async def test_tenant_search_enforces_visible_roots_and_shared_acl(
    vector_backend_factory, tmp_path, legacy_mode
):
    ctx = _ctx()
    own_uri = "viking://user/alice/resources/notes"
    cross_user_uri = "viking://user/bob/resources/notes"
    records = [
        {
            "id": "own",
            "uri": own_uri,
            "account_id": "acct",
            "context_type": "resource",
        },
        {
            "id": "cross-user",
            "uri": cross_user_uri,
            "account_id": "acct",
            "context_type": "resource",
        },
        {
            **legacy_mode,
            "id": "legacy-shared",
            "uri": "viking://agent/workflows/daily.md",
            "account_id": "acct",
            "context_type": "resource",
        },
        {
            **legacy_mode,
            "id": "default-shared",
            "uri": "viking://resources/default.md",
            "account_id": "acct",
            "context_type": "resource",
        },
        {
            "id": "direct-shared",
            "uri": "viking://resources/direct.md",
            "account_id": "acct",
            "context_type": "resource",
            "acl_mode": "inherit",
            "acl_direct_grants": ["1:user:alice"],
        },
        {
            "id": "inherited-shared",
            "uri": "viking://resources/inherited.md",
            "account_id": "acct",
            "context_type": "resource",
            "acl_mode": "inherit",
            "acl_inherited_grants": ["7:user:*"],
        },
        {
            "id": "restricted-inherited-shared",
            "uri": "viking://resources/restricted-inherited.md",
            "account_id": "acct",
            "context_type": "resource",
            "acl_mode": "restricted",
            "acl_inherited_grants": ["7:user:*"],
        },
        {
            "id": "restricted-direct-shared",
            "uri": "viking://resources/restricted-direct.md",
            "account_id": "acct",
            "context_type": "resource",
            "acl_mode": "restricted",
            "acl_direct_grants": ["1:user:alice"],
            "acl_inherited_grants": ["7:user:bob"],
        },
        {
            "id": "denied-shared",
            "uri": "viking://resources/finance/denied.md",
            "account_id": "acct",
            "context_type": "resource",
            "acl_mode": "inherit",
            "acl_direct_grants": [],
            "acl_inherited_grants": ["3:group:finance"],
        },
        {
            "id": "foreign-account",
            "uri": "viking://resources/foreign.md",
            "account_id": "other",
            "context_type": "resource",
        },
    ]

    backend = vector_backend_factory(
        config=VectorDBBackendConfig(
            backend="local", name="context", dimension=4, path=str(tmp_path / "vectors")
        )
    )
    try:
        schema = CollectionSchemas.context_collection("context", 4)
        # Exercise genuinely absent/null fields without a schema default filling them in.
        next(field for field in schema["Fields"] if field["FieldName"] == "acl_mode").pop(
            "DefaultValue"
        )
        assert await backend.create_collection("context", schema)
        acl_config = _AclConfigReader(True)
        backend.acl_manager = AclManager(backend, acl_config)
        for record in records:
            record_ctx = RequestContext(
                user=UserIdentifier(record["account_id"], ctx.user.user_id), role=Role.ADMIN
            )
            await backend._upsert_many_raw(
                [{**record, "level": 2, "vector": [1.0, 0.0, 0.0, 0.0]}], ctx=record_ctx
            )

        visible = await backend.search_in_tenant(
            ctx=ctx,
            query_vector=[1.0, 0.0, 0.0, 0.0],
            context_type="resource",
        )
        cross_user_only = await backend.search_in_tenant(
            ctx=ctx,
            query_vector=[1.0, 0.0, 0.0, 0.0],
            context_type="resource",
            target_directories=[cross_user_uri],
        )
        internal = await backend.search_in_tenant(
            ctx=RequestContext(
                user=ctx.user,
                role=ctx.role,
                bypass_acl=True,
            ),
            query_vector=[1.0, 0.0, 0.0, 0.0],
            context_type="resource",
        )
        finance = await backend.search_in_tenant(
            ctx=RequestContext(user=ctx.user, role=ctx.role, group_ids=("finance",)),
            query_vector=[1.0, 0.0, 0.0, 0.0],
            context_type="resource",
            target_directories=["viking://resources/finance"],
        )
        assert [record["id"] for record in finance] == ["denied-shared"]

        assert sorted(record["id"] for record in visible) == sorted(
            [
                "own",
                "legacy-shared",
                "default-shared",
                "direct-shared",
                "inherited-shared",
                "restricted-direct-shared",
            ]
        )
        assert cross_user_only == []
        assert sorted(record["id"] for record in internal) == sorted(
            [
                "own",
                "cross-user",
                "legacy-shared",
                "default-shared",
                "direct-shared",
                "inherited-shared",
                "restricted-inherited-shared",
                "restricted-direct-shared",
                "denied-shared",
            ]
        )

        acl_config.enabled = False
        shared = await backend.search_in_tenant(
            ctx=ctx,
            query_vector=[1.0, 0.0, 0.0, 0.0],
            context_type="resource",
        )
        assert sorted(record["id"] for record in shared) == sorted(
            [
                "own",
                "legacy-shared",
                "default-shared",
                "direct-shared",
                "inherited-shared",
                "restricted-inherited-shared",
                "restricted-direct-shared",
                "denied-shared",
            ]
        )

    finally:
        await backend.close()


def test_segment_prefix_and_visible_root_ancestor_do_not_elide_tenant_filter():
    ctx = _ctx()

    segment_prefix = _build(ctx, ["viking://resources-other/wiki"])
    ancestor = _build(ctx, ["viking://user"])

    assert segment_prefix == And(
        [
            Eq("context_type", "resource"),
            _tenant_filter(ctx),
            Or([PathScope("uri", "viking://resources-other/wiki", depth=-1)]),
        ]
    )
    assert ancestor == And(
        [
            Eq("context_type", "resource"),
            _tenant_filter(ctx),
            Or([PathScope("uri", "viking://user", depth=-1)]),
        ]
    )


def test_no_target_keeps_original_tenant_filter():
    ctx = _ctx()

    assert _build(ctx, None) == And(
        [
            Eq("context_type", "resource"),
            _tenant_filter(ctx),
        ]
    )


def test_merge_filters_wraps_raw_dict_filter():
    backend = object.__new__(VikingVectorIndexBackend)

    result = backend._merge_filters(
        {"op": "must", "field": "uri", "conds": ["viking://resources"]},
        Eq("account_id", "acct"),
    )

    assert result == And(
        [
            RawDSL({"op": "must", "field": "uri", "conds": ["viking://resources"]}),
            Eq("account_id", "acct"),
        ]
    )


def test_root_role_keeps_existing_target_only_behavior():
    ctx = _ctx(role=Role.ROOT)
    target = "viking://resources/wiki"

    assert _build(ctx, [target]) == And(
        [
            Eq("context_type", "resource"),
            Or([PathScope("uri", target, depth=-1)]),
        ]
    )


def test_actor_peer_target_retains_account_and_exact_target_scope():
    ctx = _ctx(actor_peer_id="visitor-a")
    target = "viking://user/alice/peers/visitor-a/resources/cases"

    result = _build(ctx, [target])

    assert result == And(
        [
            Eq("context_type", "resource"),
            Eq("account_id", "acct"),
            Or([PathScope("uri", target, depth=-1)]),
        ]
    )


@pytest.mark.asyncio
async def test_search_by_random_propagates_adapter_errors():
    backend = _single_account_backend(_FailingAsyncAdapter(), None)

    with pytest.raises(RuntimeError, match="search_by_random failed"):
        await backend.search_by_random(filter=Eq("uri", "viking://resources/a.md"))


@pytest.mark.asyncio
async def test_search_by_random_reuses_account_filter_for_raw_dsl():
    backend = _single_account_backend(_RecordingAsyncAdapter(), "acct")
    raw_filter = {"op": "must", "field": "uri", "conds": ["viking://resources"]}

    await backend.search_by_random(filter=raw_filter)

    assert backend._async_adapter.calls == [
        (
            "search_by_random",
            {
                "filter": And([Eq("account_id", "acct"), RawDSL(raw_filter)]),
                "limit": 10,
                "offset": 0,
                "output_fields": None,
                "advance": None,
            },
        )
    ]


@pytest.mark.asyncio
async def test_null_decay_protection_keeps_the_original_single_search_call():
    backend = object.__new__(VikingVectorIndexBackend)
    backend.acl_manager = None
    calls = []

    async def fake_search(**kwargs):
        calls.append(kwargs)
        return [{"uri": "viking://resources/doc", "_score": 0.8}]

    backend.search = fake_search
    results = await backend.search_in_tenant(
        ctx=_ctx(),
        query_vector=[1.0],
        context_type="memory",
        limit=7,
        offset=2,
    )

    assert results == [{"uri": "viking://resources/doc", "_score": 0.8}]
    assert len(calls) == 1
    assert calls[0]["limit"] == 7
    assert calls[0]["offset"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("backend_type", ["local", "vikingdb", "volcengine"])
async def test_decay_leaves_untagged_user_and_peer_events_unchanged(backend_type):
    backend = _backend_with_type(backend_type)
    calls = []

    async def fake_search(**kwargs):
        calls.append(kwargs)
        if _contains_expr(kwargs["filter"], Eq("search_tags", "memory_type=events")):
            return []
        return [
            {
                "uri": "viking://user/alice/memories/events/old",
                "level": 2,
                "updated_at": "2026-01-01T00:00:00Z",
                "_score": 0.9,
            },
            {
                "uri": "viking://user/alice/peers/peer-a/memories/events/recent",
                "level": 2,
                "updated_at": "2026-01-08T00:00:00Z",
                "_score": 0.6,
            },
            {
                "uri": "viking://user/alice/memories/preferences/pref.md",
                "level": 2,
                "updated_at": "2026-01-08T00:00:00Z",
                "_score": 0.75,
            },
        ]

    backend.search = fake_search
    results = await backend.search_in_tenant(
        ctx=_ctx(),
        query_vector=[1.0],
        context_type="memory",
        limit=3,
        events_time_decay_protection="0",
        request_now=datetime(2026, 1, 8, tzinfo=timezone.utc),
    )

    assert len(calls) == 2
    assert calls[0]["limit"] == 3
    assert [item["_score"] for item in results] == pytest.approx([0.9, 0.75, 0.6])
    assert all("_origin_score" not in item and "_time_score" not in item for item in results)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend_type", ["local", "cuvs", "http", "opengauss"])
async def test_non_cloud_decay_never_passes_advanced_ranking_options(backend_type):
    backend = _backend_with_type(backend_type)
    calls = []

    async def fake_search(**kwargs):
        calls.append(kwargs)
        return []

    backend.search = fake_search
    await backend.search_in_tenant(
        ctx=_ctx(),
        query_vector=[1.0],
        context_type="memory",
        limit=3,
        events_time_decay_protection="0",
        request_now=datetime(2026, 1, 8, tzinfo=timezone.utc),
    )

    assert len(calls) == 2
    assert all(call["advance"] is None for call in calls)
    assert all(call["return_detail_info"] is False for call in calls)


@pytest.mark.asyncio
async def test_decay_uses_the_resolved_account_backend_capabilities():
    backend = _backend_with_type("vikingdb")
    backend._get_backend_for_context = AsyncMock(return_value=SimpleNamespace(_mode="http"))
    calls = []

    async def fake_search(**kwargs):
        calls.append(kwargs)
        return []

    backend.search = fake_search
    await backend.search_in_tenant(
        ctx=_ctx(),
        query_vector=[1.0],
        context_type="memory",
        limit=3,
        events_time_decay_protection="0",
        request_now=datetime(2026, 1, 8, tzinfo=timezone.utc),
    )

    assert len(calls) == 2
    assert all(call["advance"] is None for call in calls)
    assert all(call["return_detail_info"] is False for call in calls)


@pytest.mark.asyncio
async def test_decay_rerank_prefetch_keeps_expanded_origin_candidates():
    backend = _backend_with_type("local")
    calls = []

    async def fake_search(**kwargs):
        calls.append(kwargs)
        if not _contains_expr(kwargs["filter"], Eq("search_tags", "memory_type=events")):
            return []
        candidates = [
            {
                "uri": "viking://user/alice/memories/events/fresh",
                "level": 2,
                "_score": 0.9,
                "updated_at": "2026-01-08T00:00:00Z",
            },
            {
                "uri": "viking://user/alice/peers/peer-a/memories/events/old",
                "level": 2,
                "_score": 0.8,
                "updated_at": "2026-01-01T00:00:00Z",
            },
        ]
        return candidates[: kwargs["limit"]]

    backend.search = fake_search
    results = await backend.search_in_tenant(
        ctx=_ctx(),
        query_vector=[1.0],
        context_type="memory",
        target_directories=["viking://user/alice"],
        level=[2],
        limit=1,
        events_time_decay_protection="0",
        for_rerank=True,
        request_now=datetime(2026, 1, 8, tzinfo=timezone.utc),
    )

    assert calls[0]["limit"] == 3
    assert calls[0]["advance"] is None
    assert calls[0]["return_detail_info"] is False
    assert [result["_score"] for result in results] == pytest.approx([0.9, 0.8])
    assert [result["_origin_score"] for result in results] == pytest.approx([0.9, 0.8])
    assert [result["_time_score"] for result in results] == pytest.approx([1.0, 0.5])


@pytest.mark.asyncio
async def test_cloud_decay_keeps_directory_levels_that_share_a_uri():
    backend = _backend_with_type("vikingdb")
    directory_uri = "viking://user/alice/memories/events/project"

    async def fake_search(**kwargs):
        if kwargs.get("advance"):
            return []
        return [
            {
                "uri": directory_uri,
                "context_type": "memory",
                "level": 0,
                "_score": 0.9,
            },
            {
                "uri": directory_uri,
                "context_type": "memory",
                "level": 1,
                "_score": 0.8,
            },
        ]

    backend.search = fake_search
    results = await backend.search_in_tenant(
        ctx=_ctx(),
        query_vector=[1.0],
        context_type="memory",
        limit=2,
        events_time_decay_protection="0",
        request_now=datetime(2026, 1, 8, tzinfo=timezone.utc),
    )

    assert [(result["uri"], result["level"]) for result in results] == [
        (directory_uri, 0),
        (directory_uri, 1),
    ]


@pytest.mark.asyncio
async def test_decay_applies_to_a_peer_only_target():
    backend = _backend_with_type("local")
    calls = []

    async def fake_search(**kwargs):
        calls.append(kwargs)
        if not _contains_expr(kwargs["filter"], Eq("search_tags", "memory_type=events")):
            return []
        return [
            {
                "uri": "viking://user/alice/peers/peer-a/memories/events/recent",
                "level": 2,
                "updated_at": "2026-01-08T00:00:00Z",
                "_score": 0.2,
            }
        ]

    backend.search = fake_search
    results = await backend.search_in_tenant(
        ctx=_ctx(),
        query_vector=[1.0],
        context_type="memory",
        target_directories=["viking://user/alice/peers/peer-a/memories/events"],
        level=[2],
        events_time_decay_protection="0",
        request_now=datetime(2026, 1, 8, tzinfo=timezone.utc),
    )

    assert len(calls) == 2
    assert calls[1]["limit"] == MAX_TIME_DECAY_CANDIDATES
    assert results[0]["_score"] == pytest.approx(0.2)
    assert results[0]["_origin_score"] == pytest.approx(0.2)
    assert results[0]["_time_score"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_decay_applies_under_bare_user_target():
    backend = _backend_with_type("local")
    calls = []

    async def fake_search(**kwargs):
        calls.append(kwargs)
        if _contains_expr(kwargs["filter"], Eq("search_tags", "memory_type=events")):
            return []
        return []

    backend.search = fake_search
    await backend.search_in_tenant(
        ctx=_ctx(),
        query_vector=[1.0],
        context_type="memory",
        target_directories=["viking://user"],
        level=[2],
        limit=2,
        events_time_decay_protection="0",
    )

    assert len(calls) == 2
    assert calls[0]["limit"] == 2


@pytest.mark.asyncio
async def test_decay_applies_to_children_of_a_peer_event_directory():
    backend = _backend_with_type("local")
    calls = []

    async def fake_search(**kwargs):
        calls.append(kwargs)
        if not _contains_expr(kwargs["filter"], Eq("search_tags", "memory_type=events")):
            return []
        return [
            {
                "uri": "viking://user/alice/peers/peer-a/memories/events/recent",
                "level": 2,
                "updated_at": "2026-01-08T00:00:00Z",
                "_score": 0.2,
            }
        ]

    backend.search = fake_search
    results = await backend.search_children_in_tenant(
        ctx=_ctx(),
        parent_uri="viking://user/alice/peers/peer-a/memories/events",
        query_vector=[1.0],
        context_type="memory",
        limit=2,
        events_time_decay_protection="0",
        request_now=datetime(2026, 1, 8, tzinfo=timezone.utc),
    )

    assert len(calls) == 2
    assert calls[0]["limit"] == 6
    assert results[0]["_score"] == pytest.approx(0.2)
    assert results[0]["_origin_score"] == pytest.approx(0.2)
    assert results[0]["_time_score"] == pytest.approx(1.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend_type", ["local", "vikingdb", "volcengine"])
@pytest.mark.parametrize("actor_peer_id", [None, "peer-a"])
@pytest.mark.parametrize("for_rerank", [False, True])
async def test_tagged_events_use_the_same_split_and_scores_across_backends(
    backend_type, actor_peer_id, for_rerank
):
    backend = _backend_with_type(backend_type)
    calls = []
    new_event = {
        "uri": "viking://user/alice/peers/peer-a/memories/events/new.md",
        "context_type": "memory",
        "level": 2,
        "search_tags": ["memory_type=events"],
        "updated_at": "2026-01-08T00:00:00Z",
        "_score": 0.2,
    }
    old_event = {
        "uri": "viking://user/alice/peers/peer-a/memories/events/old.md",
        "level": 2,
        "search_tags": ["memory_type=events"],
        "updated_at": "2026-01-01T00:00:00Z",
        "_score": 0.6,
    }
    preference = {
        "uri": "viking://user/alice/memories/preferences/p.md",
        "level": 2,
        "search_tags": ["memory_type=preferences"],
        "_score": 0.75,
    }

    async def fake_search(**kwargs):
        calls.append(kwargs)
        if not _contains_expr(kwargs["filter"], Eq("search_tags", "memory_type=events")):
            return [dict(preference)]
        event = dict(new_event)
        old = dict(old_event)
        if kwargs.get("advance"):
            # The cloud adapter returns the already fused score and explanations.
            event.update(_score=0.2, _origin_score=0.2, _time_score=1.0)
            old.update(_score=0.3, _origin_score=0.6, _time_score=0.5)
        return [old, event]

    backend.search = fake_search
    ctx = _ctx(actor_peer_id=actor_peer_id)
    results = await backend.search_in_tenant(
        ctx=ctx,
        query_vector=[1.0],
        context_type="memory",
        extra_filter=Eq("search_tags", "team=search"),
        limit=2,
        events_time_decay_protection="0",
        request_now=datetime(2026, 1, 8, tzinfo=timezone.utc),
        for_rerank=for_rerank,
    )

    assert len(calls) == 2
    expected_scope = _build(
        ctx, None, context_type="memory", extra_filter=Eq("search_tags", "team=search")
    )
    assert all(_contains_expr(call["filter"], expected_scope) for call in calls)
    if for_rerank:
        assert all(call["advance"] is None for call in calls)
        assert [result["_score"] for result in results] == pytest.approx([0.75, 0.6, 0.2])
        assert results[1]["_time_score"] == pytest.approx(0.5)
        assert results[2]["_time_score"] == pytest.approx(1.0)
    else:
        assert [result["uri"] for result in results] == [preference["uri"], old_event["uri"]]
        assert [result["_score"] for result in results] == pytest.approx([0.75, 0.3])


@pytest.mark.asyncio
async def test_local_tag_split_does_not_boost_fresh_events(vector_backend_factory, tmp_path):
    backend = vector_backend_factory(
        config=VectorDBBackendConfig(
            backend="local", name="context", dimension=4, path=str(tmp_path / "vectors")
        )
    )
    ctx = _ctx()
    event_uri = "viking://user/alice/peers/peer-a/memories/events/new.md"
    old_event_uri = "viking://user/alice/peers/peer-a/memories/events/old.md"
    legacy_uri = "viking://user/alice/peers/peer-b/memories/events/old.md"
    try:
        assert await backend.create_collection(
            "context", CollectionSchemas.context_collection("context", 4)
        )
        records = [
            {
                "id": f"preference-{i}",
                "uri": f"viking://user/alice/memories/preferences/{i}.md",
                "vector": [0.8, 0.6, 0.0, 0.0],
                "search_tags": ["memory_type=preferences"],
            }
            for i in range(4)
        ]
        records.extend(
            [
                {
                    "id": "new",
                    "uri": event_uri,
                    "vector": [0.2, 0.98, 0.0, 0.0],
                    "search_tags": ["memory_type=events"],
                },
                {
                    "id": "old",
                    "uri": old_event_uri,
                    "vector": [0.99, 0.1, 0.0, 0.0],
                    "search_tags": ["memory_type=events"],
                    "updated_at": "2026-01-01T00:00:00Z",
                },
                {
                    "id": "foreign-owner",
                    "uri": "viking://user/bob/memories/events/hidden.md",
                    "vector": [1.0, 0.0, 0.0, 0.0],
                    "search_tags": ["memory_type=events"],
                },
            ]
        )
        await backend._upsert_many_raw(
            [
                {
                    "account_id": "acct",
                    "context_type": "memory",
                    "level": 2,
                    "updated_at": "2026-01-08T00:00:00Z",
                    **record,
                }
                for record in records
            ],
            ctx=ctx,
        )
        options = {
            "ctx": ctx,
            "query_vector": [1.0, 0.0, 0.0, 0.0],
            "context_type": "memory",
            "limit": 1,
        }
        original = await backend.search_in_tenant(**options)
        assert original[0]["uri"] == old_event_uri
        decayed = await backend.search_in_tenant(
            **options,
            events_time_decay_protection="0",
            request_now=datetime(2026, 1, 8, tzinfo=timezone.utc),
        )
        assert "/memories/preferences/" in decayed[0]["uri"]
        replaced = await backend.update_search_tags(
            event_uri, ["team=search"], mode="replace", ctx=ctx
        )
        assert set(replaced[0]["search_tags"]) == {"memory_type=events", "team=search"}
        # A genuinely absent search_tags field must still match the other route.
        await backend._upsert_many_raw(
            [
                {
                    "id": "legacy",
                    "uri": legacy_uri,
                    "vector": [0.9, 0.44, 0.0, 0.0],
                    "account_id": "acct",
                    "context_type": "memory",
                    "level": 2,
                    "updated_at": "2026-01-01T00:00:00Z",
                }
            ],
            ctx=ctx,
        )
        decayed = await backend.search_in_tenant(
            **{**options, "limit": 10},
            events_time_decay_protection="0",
            request_now=datetime(2026, 1, 8, tzinfo=timezone.utc),
        )
        # Tagged and untagged events are both eligible, without boosting a fresh event.
        assert {event_uri, legacy_uri} <= {item["uri"] for item in decayed}
        first = await backend.search_in_tenant(
            **options,
            events_time_decay_protection="0",
            request_now=datetime(2026, 1, 8, tzinfo=timezone.utc),
        )
        assert first[0]["uri"] == legacy_uri
        assert "_time_score" not in first[0]
    finally:
        await backend.close()
