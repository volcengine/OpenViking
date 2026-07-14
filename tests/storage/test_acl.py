# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.acl import (
    ACL_CONTEXT_FIELDS,
    AclAction,
    AclEntry,
    AclLevel,
    AclManager,
    EffectiveAcl,
    acl_allows,
    acl_ancestors,
    direct_to_entries,
    entries_to_direct,
    is_implicit_manager,
)
from openviking.storage.collection_schemas import CollectionSchemas
from openviking.storage.expr import In, RawDSL
from openviking.storage.vectordb import engine as vectordb_engine
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


def _ctx(user_id: str, role: str = Role.USER, group_ids: tuple[str, ...] = ()) -> RequestContext:
    return RequestContext(user=UserIdentifier("account-1", user_id), role=role, group_ids=group_ids)


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


def test_acl_rule_model():
    acl = entries_to_direct(
        [
            AclEntry("user:bob", AclLevel.VIEWER),
            AclEntry("user:alice", AclLevel.EDITOR),
            AclEntry("user:bob", AclLevel.MANAGER),
            AclEntry("user:*", AclLevel.VIEWER),
            AclEntry("group:grp_engineering", AclLevel.VIEWER),
        ]
    )

    assert acl.read == frozenset({"user:*", "user:alice", "user:bob", "group:grp_engineering"})
    assert acl.write == frozenset({"user:alice", "user:bob"})
    assert acl.manage == frozenset({"user:bob"})
    assert [entry.to_dict() for entry in direct_to_entries(acl)] == [
        {"principal": "group:grp_engineering", "level": "viewer"},
        {"principal": "user:*", "level": "viewer"},
        {"principal": "user:alice", "level": "editor"},
        {"principal": "user:bob", "level": "manager"},
    ]

    for invalid_entry in (
        {"principal": "", "level": "viewer"},
        {"principal": "user:bad/user", "level": "viewer"},
        {"principal": "group:*", "level": "viewer"},
        {"principal": "user:bob", "level": "owner"},
    ):
        with pytest.raises(InvalidArgumentError):
            entries_to_direct([invalid_entry])

    assert acl_ancestors("viking://resources/a/b/c.md") == [
        "viking://resources",
        "viking://resources/a",
        "viking://resources/a/b",
        "viking://resources/a/b/c.md",
    ]
    for private_uri in (
        "viking://user/alice/resources/a/b.md",
        "viking://upload/private.md",
    ):
        with pytest.raises(InvalidArgumentError, match="viking://resources"):
            acl_ancestors(private_uri)

    assert is_implicit_manager(_ctx("admin", Role.ADMIN), "viking://resources/a")
    assert not is_implicit_manager(_ctx("root", Role.ROOT), "viking://resources/a")
    assert not is_implicit_manager(_ctx("alice"), "viking://resources/a")
    assert not is_implicit_manager(_ctx("alice"), "viking://user/alice/resources/project/file.md")

    inherited = entries_to_direct(
        [
            AclEntry("user:bob", AclLevel.VIEWER),
            AclEntry("user:alice", AclLevel.EDITOR),
        ]
    )
    direct = entries_to_direct([AclEntry("group:grp_ops", AclLevel.MANAGER)])
    effective = EffectiveAcl(True, direct=direct, inherited=inherited)

    assert effective.permissions.read == frozenset({"user:alice", "user:bob", "group:grp_ops"})
    assert acl_allows(effective, _ctx("alice"), AclAction.WRITE)
    assert acl_allows(effective, _ctx("carol", group_ids=("grp_ops",)), AclAction.MANAGE)
    assert not acl_allows(effective, _ctx("bob"), AclAction.WRITE)


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
    bob = _ctx("bob", group_ids=("grp_readers",))
    carol = _ctx("carol")
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

        await acl.set_direct("viking://resources", [AclEntry("user:alice", AclLevel.VIEWER)], admin)
        await acl.set_direct(old_root, [AclEntry("group:grp_readers", AclLevel.VIEWER)], admin)
        await acl.set_direct(new_parent, [AclEntry("user:alice", AclLevel.EDITOR)], admin)

        materialized = (await context_store.get_strict(["doc-l2"], ctx=admin))[0]
        assert materialized["acl_direct_read_principal_ids"] == []
        assert materialized["acl_inherited_read_principal_ids"] == [
            "group:grp_readers",
            "user:alice",
        ]

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
        assert [record["uri"] for record in bob_results] == [old_file]
        assert carol_results == []

        assert await context_store.update_uri_mapping(ctx=admin, uri=old_root, new_uri=new_root)
        assert await context_store.update_uri_mapping(ctx=admin, uri=old_file, new_uri=new_file)
        await acl.refresh_context_subtree(new_root, admin)

        effective = await acl.resolve(new_file, admin)
        assert effective.permissions.read == frozenset({"group:grp_readers", "user:alice"})
        assert effective.permissions.write == frozenset({"user:alice"})
        assert (await acl.get_direct(old_root, admin)).empty
        assert (await acl.get_direct(new_root, admin)).read == frozenset({"group:grp_readers"})
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
        assert legacy["acl_direct_read_principal_ids"] == []
        assert legacy["acl_inherited_read_principal_ids"] == []

        visible = await backend.filter(
            filter=RawDSL({"op": "must_not", "field": "acl_enabled", "conds": [True]}),
            limit=10,
            ctx=ctx,
        )
        assert [record["id"] for record in visible] == ["legacy-1"]

        acl = AclManager(backend)
        await acl.set_direct(
            "viking://resources/legacy.md",
            [AclEntry("user:bob", AclLevel.VIEWER)],
            ctx,
        )
        stored = (await backend.get_strict(["legacy-1"], ctx=ctx))[0]
        assert stored["acl_enabled"] is True
        assert stored["acl_direct_read_principal_ids"] == ["user:bob"]
        filtered = await backend.filter(
            filter=In("acl_direct_read_principal_ids", ["user:bob"]),
            limit=10,
            ctx=ctx,
        )
        assert [record["id"] for record in filtered] == ["legacy-1"]
    finally:
        await backend.close()
