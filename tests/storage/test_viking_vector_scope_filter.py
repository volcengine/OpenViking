# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import threading
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.acl import AclManager
from openviking.storage.collection_schemas import CollectionSchemas
from openviking.storage.expr import And, Eq, In, Or, PathScope, RawDSL
from openviking.storage.record_types import context_records
from openviking.storage.viking_vector_index_backend import (
    VikingVectorIndexBackend,
    _SingleAccountBackend,
)
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
    assert adapter.search_by_random.await_args.kwargs["filter"] == And(
        [_tenant_filter(ctx), context_records()]
    )


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
@pytest.mark.parametrize("acl_enabled", [False, True])
async def test_keywords_tenant_search_reuses_scope_filter_and_sets_bm25_fields(acl_enabled):
    ctx = _ctx()
    backend = object.__new__(VikingVectorIndexBackend)
    backend.acl_manager = None
    backend._acl_enabled = AsyncMock(return_value=acl_enabled)
    tenant_backend = SimpleNamespace(search_by_keywords=AsyncMock(return_value=[]))
    backend._get_backend_for_context = AsyncMock(return_value=tenant_backend)

    await backend.search_by_keywords_in_tenant(
        ctx=ctx,
        query="OAuth token",
        context_type="resource",
        target_directories=["viking://resources/docs"],
        extra_filter=Eq("status", "ready"),
        level=[2],
        limit=5,
    )

    kwargs = tenant_backend.search_by_keywords.await_args.kwargs
    assert kwargs["query"] == "OAuth token"
    assert kwargs["mode"] == "bm25"
    assert kwargs["fields"] == ["content"]
    assert kwargs["filter"] == _build(
        ctx,
        ["viking://resources/docs"],
        extra_filter=Eq("status", "ready"),
        level=[2],
        acl_enabled=acl_enabled,
    )
    backend._acl_enabled.assert_awaited_once_with(ctx)
    backend._get_backend_for_context.assert_awaited_once_with(ctx)


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
            "search_tags": ["memory_type=events"],
        },
        {
            "id": "cross-user",
            "uri": cross_user_uri,
            "account_id": "acct",
            "context_type": "resource",
            "search_tags": ["memory_type=events"],
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
            "search_tags": ["memory_type=events"],
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
            "search_tags": ["memory_type=events"],
            "acl_direct_grants": ["1:user:alice"],
            "acl_inherited_grants": ["7:user:bob"],
        },
        {
            "id": "denied-shared",
            "uri": "viking://resources/finance/denied.md",
            "account_id": "acct",
            "context_type": "resource",
            "acl_mode": "inherit",
            "search_tags": ["memory_type=events"],
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
                [
                    {
                        **record,
                        "level": 2,
                        "updated_at": "2026-01-01T00:00:00Z",
                        "vector": [1.0, 0.0, 0.0, 0.0],
                    }
                ],
                ctx=record_ctx,
            )

        decay = {
            "events_time_decay_protection": "0",
            "request_now": datetime(2026, 1, 8, tzinfo=timezone.utc),
        }
        visible = await backend.search_in_tenant(
            ctx=ctx,
            query_vector=[1.0, 0.0, 0.0, 0.0],
            context_type="resource",
            **decay,
        )
        cross_user_only = await backend.search_in_tenant(
            ctx=ctx,
            query_vector=[1.0, 0.0, 0.0, 0.0],
            context_type="resource",
            **decay,
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
            **decay,
        )
        finance = await backend.search_in_tenant(
            ctx=RequestContext(user=ctx.user, role=ctx.role, group_ids=("finance",)),
            query_vector=[1.0, 0.0, 0.0, 0.0],
            context_type="resource",
            **decay,
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
        by_id = {record["id"]: record for record in visible}
        assert by_id["own"]["_score"] == pytest.approx(0.5)
        assert by_id["own"]["_origin_score"] == pytest.approx(1.0)
        assert by_id["legacy-shared"]["_score"] == pytest.approx(1.0)
        assert "_time_score" not in by_id["legacy-shared"]
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
            **decay,
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


@pytest.mark.asyncio
async def test_tenant_search_preserves_raw_scope_across_decay_routes():
    backend = object.__new__(VikingVectorIndexBackend)
    backend.acl_manager = None
    backend.search = AsyncMock(return_value=[])
    ctx = _ctx()
    raw_filter = {"op": "must", "field": "search_tags", "conds": ["team=search"]}
    options = {
        "ctx": ctx,
        "query_vector": [1.0],
        "context_type": "resource",
        "level": [0, 1],
        "target_directories": ["viking://resources/wiki"],
        "extra_filter": raw_filter,
        "limit": 1,
        "offset": 1,
    }
    scope = And(
        [
            Eq("context_type", "resource"),
            Eq("account_id", "acct"),
            Or([PathScope("uri", "viking://resources/wiki", depth=-1)]),
            RawDSL(raw_filter),
            In("level", [0, 1]),
        ]
    )
    await backend.search_in_tenant(**options)
    call = backend.search.await_args.kwargs
    assert backend.search.await_count == 1
    assert call["filter"] == scope
    assert (call["limit"], call["offset"]) == (1, 1)
    assert "advance" not in call

    backend.search.reset_mock()
    event = {"id": "event", "_score": 0.4, "_origin_score": 0.8, "_time_score": 0.5}

    async def recall(**kwargs):
        return [event] if kwargs.get("advance") else [{"id": "ordinary", "_score": 0.7}]

    backend.search.side_effect = recall
    result = await backend.search_in_tenant(**options, events_time_decay_protection="0")
    calls = [call.kwargs for call in backend.search.await_args_list]
    assert len(calls) == 2
    other = next(call for call in calls if "advance" not in call)
    events = next(call for call in calls if "advance" in call)
    assert other["filter"] == And(
        [scope, RawDSL({"op": "must_not", "field": "search_tags", "conds": ["memory_type=events"]})]
    )
    assert events["filter"] == And([scope, Eq("search_tags", "memory_type=events")])
    assert (other["limit"], events["limit"], other["offset"], events["offset"]) == (2, 2, 0, 0)
    assert result == [event]


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
