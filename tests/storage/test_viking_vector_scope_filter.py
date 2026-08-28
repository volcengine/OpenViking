# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import pytest

from openviking.core.namespace import uri_parts
from openviking.server.identity import RequestContext, Role
from openviking.storage.expr import And, Eq, In, Or, PathScope, RawDSL
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from openviking_cli.session.user_id import UserIdentifier


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
):
    backend = object.__new__(VikingVectorIndexBackend)
    backend.acl_manager = None
    return backend._build_scope_filter(
        ctx=ctx,
        context_type=context_type,
        target_directories=targets,
        extra_filter=extra_filter,
        level=level,
    )


def _tenant_filter(ctx: RequestContext):
    backend = object.__new__(VikingVectorIndexBackend)
    backend.acl_manager = None
    return backend._tenant_filter(ctx)


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
        "viking://agent/skills/research",
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
                    PathScope("uri", "viking://agent/skills/research", depth=-1),
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
async def test_tenant_search_enforces_visible_roots_and_shared_acl():
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
            "id": "legacy-shared",
            "uri": "viking://resources/legacy.md",
            "account_id": "acct",
            "context_type": "resource",
        },
        {
            "id": "direct-shared",
            "uri": "viking://resources/direct.md",
            "account_id": "acct",
            "context_type": "resource",
            "acl_enabled": True,
            "acl_direct_grants": ["1:user:alice"],
        },
        {
            "id": "inherited-shared",
            "uri": "viking://resources/inherited.md",
            "account_id": "acct",
            "context_type": "resource",
            "acl_enabled": True,
            "acl_inherited_grants": ["3:user:*"],
        },
        {
            "id": "denied-shared",
            "uri": "viking://resources/denied.md",
            "account_id": "acct",
            "context_type": "resource",
            "acl_enabled": True,
            "acl_direct_grants": ["7:user:bob"],
        },
        {
            "id": "foreign-account",
            "uri": "viking://resources/foreign.md",
            "account_id": "other",
            "context_type": "resource",
        },
    ]

    def matches(expr, record):
        if isinstance(expr, And):
            return all(matches(cond, record) for cond in expr.conds)
        if isinstance(expr, Or):
            return any(matches(cond, record) for cond in expr.conds)
        if isinstance(expr, Eq):
            return record.get(expr.field) == expr.value
        if isinstance(expr, In):
            return any(value in expr.values for value in record.get(expr.field, []))
        if isinstance(expr, RawDSL):
            assert expr.payload["op"] == "must_not"
            return record.get(expr.payload["field"]) not in expr.payload["conds"]
        if isinstance(expr, PathScope):
            root = uri_parts(expr.path)
            path = uri_parts(str(record.get(expr.field, "")))
            if path[: len(root)] != root:
                return False
            return expr.depth == -1 or len(path) - len(root) <= expr.depth
        raise AssertionError(f"Unexpected filter expression in test: {expr!r}")

    async def fake_search(*, filter, **_kwargs):
        return [record for record in records if matches(filter, record)]

    backend = object.__new__(VikingVectorIndexBackend)
    backend.acl_manager = object()
    backend.search = fake_search

    visible = await backend.search_in_tenant(
        ctx=ctx,
        query_vector=[1.0],
        context_type="resource",
    )
    cross_user_only = await backend.search_in_tenant(
        ctx=ctx,
        query_vector=[1.0],
        context_type="resource",
        target_directories=[cross_user_uri],
    )
    internal = await backend.search_in_tenant(
        ctx=RequestContext(
            user=ctx.user,
            role=ctx.role,
            bypass_acl=True,
        ),
        query_vector=[1.0],
        context_type="resource",
    )

    assert [record["id"] for record in visible] == [
        "own",
        "legacy-shared",
        "direct-shared",
        "inherited-shared",
    ]
    assert cross_user_only == []
    assert [record["id"] for record in internal] == [
        "own",
        "cross-user",
        "legacy-shared",
        "direct-shared",
        "inherited-shared",
        "denied-shared",
    ]


def test_segment_prefix_and_visible_root_ancestor_do_not_elide_tenant_filter():
    ctx = _ctx()

    segment_prefix = _build(ctx, ["viking://resources-other/wiki"])
    ancestor = _build(ctx, ["viking://agent"])

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
            Or([PathScope("uri", "viking://agent", depth=-1)]),
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
