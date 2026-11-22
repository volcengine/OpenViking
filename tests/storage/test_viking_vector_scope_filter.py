# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from openviking.server.identity import DataAccessScope, RequestContext, Role
from openviking.storage.acl import AclAction, AclManager, acl_allows, acl_filter
from openviking.storage.collection_schemas import CollectionSchemas
from openviking.storage.expr import And, Eq, FilterExpr, In, Or, PathScope, RawDSL
from openviking.storage.scope_filter import merge_filters
from openviking.storage.vectordb_adapters.local_adapter import LocalCollectionAdapter
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


class _FailingAdapter:
    mode = "local"

    def search_by_random(self, **kwargs):
        raise RuntimeError("search_by_random failed")


class _RecordingAdapter:
    mode = "local"

    def __init__(self):
        self.filter = None

    def search_by_random(self, *, filter, **kwargs):
        self.filter = filter
        return []


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


@pytest.mark.asyncio
async def test_scroll_and_count_hide_cross_user_records(vector_backend_factory, tmp_path):
    ctx = _ctx()
    backend = vector_backend_factory(
        config=VectorDBBackendConfig(
            backend="local",
            name="context",
            dimension=4,
            path=str(tmp_path / "debug_vectors"),
        )
    )
    try:
        assert await backend.create_collection(
            "context", CollectionSchemas.context_collection("context", 4)
        )
        await backend._upsert_many_raw(
            [
                {
                    "id": "alice-memory",
                    "uri": "viking://user/alice/memories/own.md",
                    "account_id": "acct",
                    "context_type": "memory",
                    "level": 2,
                    "updated_at": "2026-01-01T00:00:00Z",
                    "vector": [1.0, 0.0, 0.0, 0.0],
                },
                {
                    "id": "bob-memory",
                    "uri": "viking://user/bob/memories/private.md",
                    "account_id": "acct",
                    "context_type": "memory",
                    "level": 2,
                    "updated_at": "2026-01-01T00:00:00Z",
                    "vector": [1.0, 0.0, 0.0, 0.0],
                },
            ],
            ctx=RequestContext(user=UserIdentifier("acct", "admin"), role=Role.ADMIN),
        )

        records, next_cursor = await backend.scroll(
            filter=Eq("context_type", "memory"),
            limit=10,
            ctx=ctx,
        )
        count = await backend.count(filter=Eq("context_type", "memory"), ctx=ctx)

        assert next_cursor is None
        assert [record["id"] for record in records] == ["alice-memory"]
        assert count == 1
    finally:
        await backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("mode", "allowed"), [("restricted", False), ("inherit", True)])
async def test_acl_resolution_preserves_revocation_and_inheritance(
    vector_backend_factory, tmp_path, mode, allowed
):
    reader = RequestContext(user=UserIdentifier("acct", "reader"), role=Role.USER)
    admin = RequestContext(user=UserIdentifier("acct", "admin"), role=Role.ADMIN)
    parent = "viking://resources/parent"
    target = f"{parent}/target"
    leaf = f"{target}/rules.md"
    grants = ["1:user:reader"]
    backend = vector_backend_factory(
        config=VectorDBBackendConfig(
            backend="local",
            name="context",
            dimension=4,
            path=str(tmp_path / "acl_vectors"),
        )
    )
    search = {
        "ctx": reader,
        "query_vector": [1.0, 0.0, 0.0, 0.0],
        "target_directories": [target],
    }
    try:
        assert await backend.create_collection(
            "context", CollectionSchemas.context_collection("context", 4)
        )
        backend.acl_manager = AclManager(backend, _AclConfigReader(True))
        await backend._upsert_many_raw(
            [
                {
                    "id": record_id,
                    "uri": uri,
                    "account_id": "acct",
                    "context_type": "resource",
                    "level": 2,
                    "updated_at": "2026-01-01T00:00:00Z",
                    "vector": [1.0, 0.0, 0.0, 0.0],
                    "acl_mode": record_mode,
                    "acl_direct_grants": direct,
                    "acl_inherited_grants": inherited,
                }
                for record_id, uri, record_mode, direct, inherited in (
                    ("parent", parent, "restricted", grants, []),
                    ("dir", target, mode, grants, grants),
                    ("file", leaf, "inherit", [], grants),
                )
            ],
            ctx=admin,
        )
        assert {row["id"] for row in await backend.search_in_tenant(**search)} == {"dir", "file"}

        await backend.acl_manager.set_acl(target, [], admin)
        saved = {row["id"]: row for row in await backend.get_strict(["dir", "file"], ctx=admin)}
        assert saved["dir"]["acl_mode"] == mode
        assert saved["dir"]["acl_direct_grants"] == ["none"]
        assert saved["dir"]["acl_inherited_grants"] == grants
        assert saved["file"]["acl_direct_grants"] == ["none"]
        assert saved["file"]["acl_inherited_grants"] == (grants if allowed else ["none"])
        visible = {"dir", "file"} if allowed else set()
        assert {row["id"] for row in await backend.search_in_tenant(**search)} == visible

        # Resolution must still see a restricted record hidden from this caller.
        acl = await backend.acl_manager.resolve(leaf, reader)
        assert acl_allows(acl, reader, AclAction.READ) is allowed

        # An ordinary update must not restore revoked access or discard inheritance.
        updated = {**saved["dir"], "abstract": "updated"}
        assert await backend.upsert(updated, ctx=admin) == "dir"
        assert (await backend.get_strict(["dir"], ctx=admin))[0] == updated
        assert {row["id"] for row in await backend.search_in_tenant(**search)} == visible
    finally:
        await backend.close()


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
async def test_keywords_tenant_search_keeps_scope_with_candidate_floor(acl_enabled):
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
        keywords_args={"post_process_input_limit": 3},
    )

    kwargs = tenant_backend.search_by_keywords.await_args.kwargs
    assert kwargs["query"] == "OAuth token"
    assert kwargs["mode"] == "bm25"
    assert kwargs["fields"] == ["content"]
    assert kwargs["advance"] == {"post_process_input_limit": 5}
    assert kwargs["filter"] == _build(
        ctx,
        ["viking://resources/docs"],
        extra_filter=Eq("status", "ready"),
        level=[2],
        acl_enabled=acl_enabled,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "legacy_mode,action",
    [
        ({}, AclAction.READ),
        ({"acl_mode": None}, AclAction.WRITE),
        ({"acl_mode": "none"}, AclAction.MANAGE),
    ],
)
async def test_tenant_search_enforces_visible_roots_and_shared_acl(
    vector_backend_factory, tmp_path, legacy_mode, action
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
            "acl_mode": "restricted",
            "acl_direct_grants": [],
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

        # The same ACL predicate supports existence checks for read, write and manage.
        finance_ctx = RequestContext(user=ctx.user, role=ctx.role, group_ids=("finance",))
        account_backend = await backend.get_account_backend(ctx.account_id)
        denied = await account_backend.strict_query(
            filter=And(
                [
                    Eq("account_id", ctx.account_id),
                    PathScope("uri", "viking://resources", depth=-1),
                    acl_filter(finance_ctx, action, denied=True),
                ]
            ),
            limit=100,
            output_fields=["id"],
        )
        expected_denied = {"restricted-inherited-shared"}
        if action != AclAction.READ:
            expected_denied.update({"direct-shared", "restricted-direct-shared"})
        if action == AclAction.MANAGE:
            expected_denied.add("denied-shared")
        assert {record["id"] for record in denied} == expected_denied

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
    backend = _SingleAccountBackend(VectorDBBackendConfig(), None, _FailingAdapter())

    with pytest.raises(RuntimeError, match="search_by_random failed"):
        await backend.search_by_random(filter=Eq("uri", "viking://resources/a.md"))


@pytest.mark.asyncio
async def test_search_by_random_reuses_account_filter_for_raw_dsl():
    adapter = _RecordingAdapter()
    backend = _SingleAccountBackend(VectorDBBackendConfig(), "acct", adapter)
    raw_filter = {"op": "must", "field": "uri", "conds": ["viking://resources"]}

    await backend.search_by_random(filter=raw_filter)

    assert adapter.filter == And([Eq("account_id", "acct"), RawDSL(raw_filter)])


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["count", "scroll", "search_by_random", "search_by_keywords"])
@pytest.mark.parametrize("acl_enabled", [False, True])
async def test_scoped_reads_share_visibility_specialization(method, acl_enabled):
    ctx = _ctx()
    request = And(
        [
            PathScope("uri", "viking://resources/docs", depth=1),
            Eq("level", 2),
            RawDSL({"op": "must", "field": "search_tags", "conds": ["team=one"]}),
        ]
    )
    result = {"count": 4, "scroll": ([], None)}.get(method, [])
    call = AsyncMock(return_value=result)
    backend = object.__new__(VikingVectorIndexBackend)
    acl_read = AsyncMock(return_value=acl_enabled)
    with (
        patch.object(backend, "_acl_enabled", acl_read),
        patch.object(
            backend,
            "_get_backend_for_context",
            AsyncMock(return_value=SimpleNamespace(**{method: call})),
        ),
    ):
        assert await getattr(backend, method)(filter=request, ctx=ctx) == result

    tenant: FilterExpr = Eq("account_id", "acct")
    if acl_enabled:
        tenant = And([tenant, acl_filter(ctx, AclAction.READ)])
    assert call.await_args is not None
    assert call.await_args.kwargs["filter"] == And([request, tenant])
    assert call.await_args.kwargs["filter"].conds[0] is request
    acl_read.assert_awaited_once_with(ctx)


@pytest.mark.parametrize("acl_enabled", [False, True])
def test_compiled_scoped_dsl_keeps_request_and_acl_without_broad_paths(acl_enabled):
    backend = object.__new__(VikingVectorIndexBackend)
    ctx = _ctx()
    request = And(
        [
            PathScope("uri", "viking://resources/docs", depth=1),
            Eq("level", 2),
            RawDSL({"op": "must", "field": "search_tags", "conds": ["team=one"]}),
        ]
    )
    adapter = LocalCollectionAdapter("context", "", "default")
    before = adapter._compile_filter(
        merge_filters(request, backend._tenant_filter(ctx, acl_enabled=acl_enabled))
    )
    after = adapter._compile_filter(
        merge_filters(request, backend._scoped_tenant_filter(ctx, request, acl_enabled=acl_enabled))
    )
    account = Eq("account_id", "acct")
    expected = And([account, acl_filter(ctx, AclAction.READ)]) if acl_enabled else account
    assert after == {
        "op": "and",
        "conds": [adapter._compile_filter(request), adapter._compile_filter(expected)],
    }
    assert before["conds"][0] == after["conds"][0]


@pytest.mark.parametrize("acl_enabled", [False, True])
@pytest.mark.parametrize("role", [Role.USER, Role.ADMIN, Role.ROOT])
def test_scoped_tenant_filter_preserves_existing_role_rules(acl_enabled, role):
    ctx = _ctx(role=role)
    backend = object.__new__(VikingVectorIndexBackend)
    request = And([PathScope("uri", "viking://resources/docs"), Eq("level", 2)])
    account = Eq("account_id", "acct")
    expected: FilterExpr | None = None if role == Role.ROOT else account
    if acl_enabled and role == Role.USER:
        expected = And([account, acl_filter(ctx, AclAction.READ)])
    assert backend._scoped_tenant_filter(ctx, request, acl_enabled=acl_enabled) == expected


def test_scope_fallback_preserves_delegated_mixed_and_unknown_access():
    backend = object.__new__(VikingVectorIndexBackend)
    ctx = _ctx()
    mixed = Or(
        [
            PathScope("uri", "viking://resources/docs"),
            PathScope("uri", "viking://user/alice/memories"),
        ]
    )
    assert backend._scoped_tenant_filter(ctx, mixed, acl_enabled=True) == _tenant_filter(
        ctx, acl_enabled=True
    )
    assert backend._scoped_tenant_filter(ctx, mixed, acl_enabled=False) == Eq("account_id", "acct")
    for request in (
        None,
        Eq("context_type", "memory"),
        Or([PathScope("uri", "viking://resources/docs"), Eq("level", 2)]),
        PathScope("uri", "viking://user"),
        PathScope("uri", "viking://resources-other/docs"),
        PathScope("uri", "viking://user/bob/memories"),
        RawDSL({"op": "must", "field": "uri", "conds": ["/resources/docs"]}),
    ):
        assert backend._scoped_tenant_filter(ctx, request, acl_enabled=False) == _tenant_filter(ctx)
    access = SimpleNamespace(
        operation=AclAction.READ,
        effective_targets=("viking://user/bob/memories",),
        query_filter=lambda: RawDSL({"op": "must", "field": "owner", "conds": ["bob"]}),
    )
    delegated = replace(ctx, role=Role(Role.GLOBAL), data_access=cast(DataAccessScope, access))
    request = PathScope("uri", "viking://user/bob/memories")
    assert backend._scoped_tenant_filter(delegated, request, acl_enabled=False) == _tenant_filter(
        delegated
    )
    bypass = replace(ctx, bypass_acl=True)
    assert backend._scoped_tenant_filter(bypass, request, acl_enabled=True) == Eq(
        "account_id", "acct"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["count", "scroll", "search_by_random", "search_by_keywords"])
async def test_scope_optimization_does_not_suppress_acl_configuration_errors(method):
    adapter_call = AsyncMock()
    backend = object.__new__(VikingVectorIndexBackend)
    with (
        patch.object(
            backend,
            "_get_backend_for_context",
            AsyncMock(return_value=SimpleNamespace(**{method: adapter_call})),
        ),
        patch.object(
            backend,
            "_acl_enabled",
            AsyncMock(side_effect=RuntimeError("ACL configuration unavailable")),
        ),
        pytest.raises(RuntimeError, match="ACL configuration unavailable"),
    ):
        await getattr(backend, method)(
            filter=PathScope("uri", "viking://resources/docs"), ctx=_ctx()
        )
    adapter_call.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [Role.USER, Role.ADMIN])
@pytest.mark.parametrize("scope_kind", ["subtree", "finite", "exact", "multiple"])
async def test_scoped_count_and_scroll_match_original_acl_result_sets(
    vector_backend_factory, tmp_path, role, scope_kind
):
    backend = vector_backend_factory(
        config=VectorDBBackendConfig(
            backend="local", name="context", dimension=4, path=str(tmp_path / "vectors")
        )
    )
    config = _AclConfigReader(False)
    backend.acl_manager = AclManager(backend, config)
    target = "viking://resources/docs"
    ctx = replace(_ctx(role=role), group_ids=("finance",))
    common: dict[str, Any] = {
        "account_id": "acct",
        "context_type": "resource",
        "level": 2,
        "updated_at": "2026-01-01T00:00:00Z",
        "vector": [1.0, 0.0, 0.0, 0.0],
        "search_tags": ["team=one"],
    }
    rows: list[dict[str, Any]] = [
        {"id": "public", "uri": target + "/public.md"},
        {
            "id": "direct",
            "uri": target + "/direct.md",
            "acl_mode": "restricted",
            "acl_direct_grants": ["1:user:alice"],
        },
        {
            "id": "inherited",
            "uri": target + "/nested/inherited.md",
            "acl_mode": "inherit",
            "acl_inherited_grants": ["1:user:alice"],
        },
        {
            "id": "denied",
            "uri": target + "/nested/denied.md",
            "acl_mode": "restricted",
            "acl_direct_grants": ["1:user:bob"],
        },
        {
            "id": "group",
            "uri": target + "/group.md",
            "acl_mode": "restricted",
            "acl_direct_grants": ["1:group:finance"],
        },
        {"id": "abstract", "uri": target, "level": 0},
        {"id": "overview", "uri": target, "level": 1},
        {"id": "outside", "uri": "viking://resources/elsewhere/a.md"},
        {"id": "other-tag", "uri": target + "/other.md", "search_tags": ["team=other"]},
        {"id": "own", "uri": "viking://user/alice/memories/a.md"},
        {"id": "other-user", "uri": "viking://user/bob/memories/a.md"},
        {"id": "foreign-account", "uri": target + "/foreign.md", "account_id": "other"},
    ]
    try:
        assert await backend.create_collection(
            "context", CollectionSchemas.context_collection("context", 4)
        )
        for row in rows:
            payload = {**common, **row}
            record_ctx = RequestContext(
                user=UserIdentifier(payload["account_id"], "alice"), role=Role(Role.ROOT)
            )
            await backend._upsert_many_raw([payload], ctx=record_ctx)
        scopes: dict[str, FilterExpr] = {
            "subtree": PathScope("uri", target),
            "finite": PathScope("uri", target, depth=1),
            "exact": Eq("uri", target + "/direct.md"),
            "multiple": Or(
                [PathScope("uri", target + "/nested"), Eq("uri", target + "/direct.md")]
            ),
        }
        candidates = {
            "subtree": {"public", "direct", "inherited", "denied", "group"},
            "finite": {"public", "direct", "group"},
            "exact": {"direct"},
            "multiple": {"direct", "inherited", "denied"},
        }
        request = And([scopes[scope_kind], Eq("level", 2), Eq("search_tags", "team=one")])
        for enabled in (False, True, False):
            config.enabled = enabled
            raw_backend = await backend._get_backend_for_context(ctx)
            original = merge_filters(request, backend._tenant_filter(ctx, acl_enabled=enabled))
            before, _ = await raw_backend.scroll(filter=original, limit=100)
            after, cursor = await backend.scroll(filter=request, limit=100, ctx=ctx)
            expected = candidates[scope_kind].copy()
            if enabled and role == Role.USER:
                expected.discard("denied")
            assert {row["id"] for row in before} == {row["id"] for row in after} == expected
            assert (
                await backend.count(filter=request, ctx=ctx)
                == await raw_backend.count(filter=original)
                == len(expected)
            )
            assert cursor is None
        config.enabled = True
        expected = candidates[scope_kind].copy()
        if role == Role.USER:
            expected.discard("denied")
        first_page, cursor = await backend.scroll(filter=request, limit=1, ctx=ctx)
        second_page = []
        if cursor is not None:
            second_page, _ = await backend.scroll(filter=request, limit=100, cursor=cursor, ctx=ctx)
        assert {row["id"] for row in first_page}.isdisjoint(row["id"] for row in second_page)
        assert {row["id"] for row in first_page + second_page} == expected
    finally:
        await backend.close()
