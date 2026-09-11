# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from typing import cast

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.acl import AclManager, AclMode
from openviking.storage.collection_schemas import CollectionSchemas
from openviking.storage.expr import And, Eq, In, Or, PathScope
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
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


class _AclScanStore:
    def __init__(self, records, pages, expected_count):
        self.records = records
        self.pages = list(pages)
        self.expected_count = expected_count
        self.upserted = []

    async def scroll(self, **kwargs):
        raise AssertionError(f"unstable scroll used: {kwargs}")

    async def _strict_transfer_count(self, ctx, filter):
        del ctx
        return self.expected_count if isinstance(filter, PathScope) else 0

    async def _strict_transfer_page(self, ctx, filter, **kwargs):
        del ctx, kwargs
        if not isinstance(filter, PathScope):
            return [], None
        return self.pages.pop(0)

    async def get_strict(self, ids, *, ctx):
        del ctx
        return [dict(self.records[record_id]) for record_id in ids]

    async def _upsert_many_raw(self, records, *, ctx):
        del ctx
        ids = [str(record["id"]) for record in records]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate record id")
        self.upserted = list(records)
        return ids


@pytest.mark.asyncio
async def test_set_acl_updates_every_record_beyond_first_page():
    ctx = _ctx()
    root = "viking://resources/large-tree"
    refs = [{"id": f"record-{index:03d}"} for index in range(501)]
    records = {
        ref["id"]: {
            "id": ref["id"],
            "uri": root if index == 0 else f"{root}/file-{index:03d}.md",
        }
        for index, ref in enumerate(refs)
    }
    pages = [
        (refs[:500], "500"),
        (refs[500:], None),
    ]
    store = _AclScanStore(records, pages, len(refs))

    manager = AclManager(cast(VikingVectorIndexBackend, store))

    result = await manager.set_acl(root, [], ctx, acl_mode=AclMode.RESTRICTED)

    assert result.mode == AclMode.RESTRICTED
    assert [record["id"] for record in store.upserted] == [ref["id"] for ref in refs]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("expected_count", "pages", "error"),
    [
        pytest.param(
            501,
            [
                ([{"id": f"record-{index:03d}"} for index in range(500)], "500"),
                ([{"id": "record-499"}, {"id": "record-500"}], None),
            ],
            "duplicate vector record record-499",
            id="duplicate-id",
        ),
        pytest.param(
            501,
            [([{"id": f"record-{index:03d}"} for index in range(500)], None)],
            "cursor ended after 500 of 501 records",
            id="early-cursor-end",
        ),
        pytest.param(
            1,
            [([], None)],
            "scan ended after 0 of 1 records",
            id="empty-page",
        ),
        pytest.param(
            501,
            [
                ([{"id": f"record-{index:03d}"} for index in range(250)], "250"),
                ([{"id": f"record-{index:03d}"} for index in range(250, 500)], "250"),
            ],
            "scroll cursor repeated: 250",
            id="repeated-cursor",
        ),
        pytest.param(
            1,
            [([{"uri": "viking://resources/large-tree/file.md"}], None)],
            "record without an ID",
            id="missing-id",
        ),
        pytest.param(
            1,
            [([{"id": "record-000"}, {"id": "record-001"}], None)],
            "returned 2 records but count was 1",
            id="more-records-than-count",
        ),
    ],
)
async def test_set_acl_rejects_incomplete_subtree_pagination(expected_count, pages, error):
    ctx = _ctx()
    root = "viking://resources/large-tree"
    store = _AclScanStore({}, pages, expected_count)
    manager = AclManager(cast(VikingVectorIndexBackend, store))

    with pytest.raises(RuntimeError, match=error):
        await manager.set_acl(root, [], ctx, acl_mode=AclMode.RESTRICTED)

    assert store.upserted == []


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
@pytest.mark.parametrize("legacy_mode", [{}, {"acl_mode": None}, {"acl_mode": "none"}])
async def test_tenant_search_enforces_visible_roots_and_shared_acl(tmp_path, legacy_mode):
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
            "uri": "viking://resources/legacy.md",
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
            "acl_inherited_grants": ["3:user:*"],
        },
        {
            "id": "restricted-inherited-shared",
            "uri": "viking://resources/restricted-inherited.md",
            "account_id": "acct",
            "context_type": "resource",
            "acl_mode": "restricted",
            "acl_inherited_grants": ["3:user:*"],
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
            "uri": "viking://resources/denied.md",
            "account_id": "acct",
            "context_type": "resource",
            "acl_mode": "inherit",
            "acl_direct_grants": ["7:user:bob"],
        },
        {
            "id": "foreign-account",
            "uri": "viking://resources/foreign.md",
            "account_id": "other",
            "context_type": "resource",
        },
    ]

    backend = VikingVectorIndexBackend(
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
        backend.acl_manager = AclManager(backend)
        backend.acl_manager.set_enabled(ctx.account_id, True)
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

        assert sorted(record["id"] for record in visible) == sorted(
            [
                "own",
                "legacy-shared",
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
                "direct-shared",
                "inherited-shared",
                "restricted-inherited-shared",
                "restricted-direct-shared",
                "denied-shared",
            ]
        )

        backend.acl_manager.set_enabled(ctx.account_id, False)
        shared = await backend.search_in_tenant(
            ctx=ctx,
            query_vector=[1.0, 0.0, 0.0, 0.0],
            context_type="resource",
        )
        assert sorted(record["id"] for record in shared) == sorted(
            [
                "own",
                "legacy-shared",
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
