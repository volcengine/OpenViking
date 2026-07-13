# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.acl import (
    ACL_CONTEXT_FIELDS,
    AclEntry,
    AclManager,
    EffectiveAcl,
    acl_allows,
    acl_ancestors,
    direct_to_entries,
    entries_to_direct,
    is_implicit_manager,
)
from openviking.storage.collection_schemas import CollectionSchemas
from openviking.storage.expr import In, PathScope, RawDSL
from openviking.storage.vectordb import engine as vectordb_engine
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


def _ctx(user_id: str, role: str = Role.USER) -> RequestContext:
    return RequestContext(user=UserIdentifier("account-1", user_id), role=role)


async def _upsert_context(
    store: VikingVectorIndexBackend,
    ctx: RequestContext,
    record_id: str,
    uri: str,
    level: int,
) -> None:
    assert await store.upsert(
        {
            "id": record_id,
            "uri": uri,
            "account_id": ctx.account_id,
            "context_type": "resource",
            "level": level,
            "content": uri,
            "vector": [1.0, 0.0, 0.0, 0.0],
        },
        ctx=ctx,
    )


def test_acl_entries_expand_levels_and_keep_highest_duplicate():
    acl = entries_to_direct(
        [
            AclEntry("bob", "viewer"),
            AclEntry("alice", "editor"),
            AclEntry("bob", "manager"),
            AclEntry("*", "viewer"),
        ]
    )

    assert acl.read == frozenset({"*", "alice", "bob"})
    assert acl.write == frozenset({"alice", "bob"})
    assert acl.manage == frozenset({"bob"})
    assert [entry.to_dict() for entry in direct_to_entries(acl)] == [
        {"user_id": "*", "level": "viewer"},
        {"user_id": "alice", "level": "editor"},
        {"user_id": "bob", "level": "manager"},
    ]


@pytest.mark.parametrize(
    "entry",
    [
        {"user_id": "", "level": "viewer"},
        {"user_id": "bad/user", "level": "viewer"},
        {"user_id": "bob", "level": "owner"},
    ],
)
def test_acl_entries_reject_invalid_values(entry):
    with pytest.raises(InvalidArgumentError):
        entries_to_direct([entry])


def test_acl_ancestors_cover_every_resource_level():
    assert acl_ancestors("viking://resources/a/b/c.md") == [
        "viking://resources",
        "viking://resources/a",
        "viking://resources/a/b",
        "viking://resources/a/b/c.md",
    ]
    assert acl_ancestors("viking://user/alice/resources/a/b.md") == [
        "viking://user/alice/resources",
        "viking://user/alice/resources/a",
        "viking://user/alice/resources/a/b.md",
    ]
    with pytest.raises(InvalidArgumentError):
        acl_ancestors("viking://upload/private.md")


def test_implicit_managers_follow_resource_ownership():
    assert is_implicit_manager(_ctx("admin", Role.ADMIN), "viking://resources/a")
    assert not is_implicit_manager(_ctx("root", Role.ROOT), "viking://resources/a")
    assert not is_implicit_manager(_ctx("alice"), "viking://resources/a")
    assert is_implicit_manager(_ctx("alice"), "viking://user/alice/resources/project/file.md")
    assert not is_implicit_manager(
        _ctx("root", Role.ROOT), "viking://user/alice/resources/project/file.md"
    )
    assert not is_implicit_manager(
        _ctx("bob", Role.ADMIN), "viking://user/alice/resources/project/file.md"
    )


def test_effective_acl_adds_direct_and_inherited_permissions():
    inherited = entries_to_direct([AclEntry("bob", "viewer"), AclEntry("alice", "editor")])
    direct = entries_to_direct([AclEntry("carol", "manager")])
    effective = EffectiveAcl(True, direct=direct, inherited=inherited)

    assert effective.permissions.read == frozenset({"alice", "bob", "carol"})
    assert acl_allows(effective, "alice", "write")
    assert acl_allows(effective, "carol", "manage")
    assert not acl_allows(effective, "bob", "write")


@pytest.mark.asyncio
async def test_context_acl_inheritance_filter_and_move(tmp_path):
    if not getattr(vectordb_engine, "PersistStore", None):
        pytest.skip("local persistent vectordb engine is unavailable")

    config = VectorDBBackendConfig(
        backend="local",
        name="acl_test_context",
        dimension=4,
        path=str(tmp_path),
    )
    context_store = VikingVectorIndexBackend(config)
    admin = _ctx("admin", Role.ADMIN)
    bob = _ctx("bob")
    carol = _ctx("carol")
    root = _ctx("root", Role.ROOT)
    old_root = "viking://resources/source"
    old_file = f"{old_root}/doc.md"
    new_parent = "viking://resources/destination"
    new_root = f"{new_parent}/source"
    new_file = f"{new_root}/doc.md"

    try:
        assert await context_store.create_collection(
            config.name,
            CollectionSchemas.context_collection(config.name, 4),
        )
        acl = AclManager(context_store)
        await _upsert_context(context_store, admin, "resources-l0", "viking://resources", 0)
        await _upsert_context(context_store, admin, "source-l0", old_root, 0)
        await _upsert_context(context_store, admin, "doc-l2", old_file, 2)
        await _upsert_context(context_store, admin, "destination-l0", new_parent, 0)

        await acl.set_direct("viking://resources", [AclEntry("alice", "viewer")], admin)
        await acl.set_direct(old_root, [AclEntry("bob", "viewer")], admin)
        await acl.set_direct(new_parent, [AclEntry("alice", "editor")], admin)

        await context_store.upsert(
            {
                "id": "doc-l2",
                "uri": old_file,
                "account_id": admin.account_id,
                "context_type": "resource",
                "level": 2,
                "content": "reindexed without ACL fields",
                "vector": [1.0, 0.0, 0.0, 0.0],
            },
            ctx=admin,
        )
        materialized = (await context_store.get_strict(["doc-l2"], ctx=admin))[0]
        assert materialized["acl_direct_read_user_ids"] == []
        assert materialized["acl_inherited_read_user_ids"] == ["alice", "bob"]

        bob_results = await context_store.search_in_tenant(
            ctx=bob,
            query_vector=[1.0, 0.0, 0.0, 0.0],
            target_directories=[old_root],
            level=[2],
        )
        carol_results = await context_store.search_in_tenant(
            ctx=carol,
            query_vector=[1.0, 0.0, 0.0, 0.0],
            target_directories=[old_root],
            level=[2],
        )
        root_results = await context_store.search_in_tenant(
            ctx=root,
            query_vector=[1.0, 0.0, 0.0, 0.0],
            target_directories=[old_root],
            level=[2],
        )
        assert [record["uri"] for record in bob_results] == [old_file]
        assert carol_results == []
        assert root_results == []
        assert await context_store.count_in_tenant(bob, PathScope("uri", old_root, depth=-1)) == 2
        assert await context_store.count_in_tenant(carol, PathScope("uri", old_root, depth=-1)) == 0

        assert await context_store.update_uri_mapping(ctx=admin, uri=old_root, new_uri=new_root)
        assert await context_store.update_uri_mapping(ctx=admin, uri=old_file, new_uri=new_file)
        await acl.refresh_context_subtree(new_root, admin)

        effective = await acl.resolve(new_file, admin)
        assert effective.permissions.read == frozenset({"alice", "bob"})
        assert effective.permissions.write == frozenset({"alice"})
        assert (await acl.get_direct(old_root, admin)).empty
        assert (await acl.get_direct(new_root, admin)).read == frozenset({"bob"})

        new_context = await context_store.filter(
            filter=In("uri", [new_file]),
            limit=10,
            ctx=admin,
        )
        assert new_context[0]["acl_direct_read_user_ids"] == []
        assert new_context[0]["acl_inherited_read_user_ids"] == ["alice", "bob"]
        assert new_context[0]["acl_inherited_write_user_ids"] == ["alice"]
    finally:
        await context_store.close()


@pytest.mark.asyncio
async def test_local_acl_schema_migration_requires_no_record_backfill(tmp_path):
    if not getattr(vectordb_engine, "PersistStore", None):
        pytest.skip("local persistent vectordb engine is unavailable")

    config = VectorDBBackendConfig(
        backend="local",
        name="acl_migration_context",
        dimension=4,
        path=str(tmp_path),
    )
    backend = VikingVectorIndexBackend(config)
    ctx = _ctx("admin", Role.ADMIN)
    current_schema = CollectionSchemas.context_collection(config.name, 4)
    legacy_schema = {
        **current_schema,
        "Fields": [
            field
            for field in current_schema["Fields"]
            if field["FieldName"] not in ACL_CONTEXT_FIELDS
        ],
        "ScalarIndex": [
            field for field in current_schema["ScalarIndex"] if field not in ACL_CONTEXT_FIELDS
        ],
    }

    try:
        assert await backend.create_collection(config.name, legacy_schema)
        await _upsert_context(backend, ctx, "legacy-1", "viking://resources/legacy.md", 2)

        await backend.update_collection_schema(
            current_schema["Fields"], current_schema["ScalarIndex"]
        )
        legacy = (await backend.get_strict(["legacy-1"], ctx=ctx))[0]
        assert legacy["acl_enabled"] is False
        assert legacy["acl_direct_read_user_ids"] == []
        assert legacy["acl_inherited_read_user_ids"] == []

        visible = await backend.filter(
            filter=RawDSL({"op": "must_not", "field": "acl_enabled", "conds": [True]}),
            limit=10,
            ctx=ctx,
        )
        assert [record["id"] for record in visible] == ["legacy-1"]

        acl = AclManager(backend)
        await acl.set_direct("viking://resources/legacy.md", [AclEntry("bob", "viewer")], ctx)
        stored = (await backend.get_strict(["legacy-1"], ctx=ctx))[0]
        assert stored["acl_enabled"] is True
        assert stored["acl_direct_read_user_ids"] == ["bob"]
        filtered = await backend.filter(
            filter=In("acl_direct_read_user_ids", ["bob"]),
            limit=10,
            ctx=ctx,
        )
        assert [record["id"] for record in filtered] == ["legacy-1"]
    finally:
        await backend.close()
