from typing import Any, cast

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.acl import AclAction, AclLevel, AclManager, DirectAcl, acl_allows
from openviking.storage.expr import Or, PathScope
from openviking_cli.session.user_id import UserIdentifier


class _EmptyContextStore:
    async def scroll(self, **_kwargs):
        return [], None


class _ContextStore:
    def __init__(self, records):
        self.records = {record["id"]: dict(record) for record in records}
        self.upsert_count = 0
        self.scroll_count = 0

    async def scroll(self, *, filter, **_kwargs):
        self.scroll_count += 1
        raw_scopes = filter.conds if isinstance(filter, Or) else [filter]
        assert all(isinstance(scope, PathScope) for scope in raw_scopes)
        scopes = cast(list[PathScope], raw_scopes)
        matched = []
        for record in self.records.values():
            uri = record["uri"]
            if any(
                uri == scope.path
                or (scope.depth == -1 and uri.startswith(scope.path.rstrip("/") + "/"))
                for scope in scopes
            ):
                matched.append(dict(record))
        return matched, None

    async def get_strict(self, ids, **_kwargs):
        return [dict(self.records[record_id]) for record_id in ids]

    async def _upsert_many_raw(self, records, **_kwargs):
        self.upsert_count += len(records)
        for record in records:
            self.records[record["id"]] = dict(record)
        return [record["id"] for record in records]


@pytest.mark.parametrize(
    "raw_grants",
    [
        [],
        [""],
        ["", ""],
        ("", ""),
    ],
)
def test_context_fields_ignore_empty_grants(raw_grants):
    acl = DirectAcl.from_context_fields(
        {"acl_direct_grants": raw_grants},
        "acl_direct",
    )

    assert acl.empty
    assert acl.entries == ()


def test_context_fields_ignore_empty_grants_beside_valid_grants():
    acl = DirectAcl.from_context_fields(
        {"acl_inherited_grants": ["", "3:user:alice", ""]},
        "acl_inherited",
    )

    assert len(acl.entries) == 1
    assert acl.entries[0].principal == "user:alice"
    assert acl.entries[0].level == AclLevel.WRITE


@pytest.mark.parametrize(
    "raw_grants",
    [
        [" "],
        ["invalid"],
        ["", "invalid"],
        ["", 1],
    ],
)
def test_context_fields_reject_nonempty_invalid_grants(raw_grants):
    with pytest.raises(RuntimeError, match="Invalid ACL grant token"):
        DirectAcl.from_context_fields(
            {"acl_direct_grants": raw_grants},
            "acl_direct",
        )


def test_context_fields_reject_nonlist_grants():
    with pytest.raises(RuntimeError, match="expected a list"):
        DirectAcl.from_context_fields(
            {"acl_direct_grants": "3:user:alice"},
            "acl_direct",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("level", list(AclLevel))
async def test_resources_root_uses_configured_implicit_acl_level(level):
    manager = AclManager(cast(Any, _EmptyContextStore()), resources_root_acl_level=level)
    ctx = RequestContext(user=UserIdentifier("account", "alice"), role=Role(Role.USER))

    acl = await manager.resolve("viking://resources", ctx)

    assert acl_allows(acl, ctx, AclAction.READ)
    assert acl_allows(acl, ctx, AclAction.WRITE) is (level != AclLevel.READ)
    assert acl_allows(acl, ctx, AclAction.MANAGE) is (level == AclLevel.MANAGE)


@pytest.mark.asyncio
async def test_resources_root_level_change_recomputes_existing_descendants():
    store = _ContextStore(
        [
            {
                "id": "root",
                "uri": "viking://resources",
                "acl_mode": "inherit",
                "acl_direct_grants": ["7:user:*"],
                "acl_inherited_grants": [],
            },
            {
                "id": "child",
                "uri": "viking://resources/existing",
                "acl_mode": "inherit",
                "acl_direct_grants": [],
                "acl_inherited_grants": ["7:user:*"],
            },
        ]
    )
    manager = AclManager(cast(Any, store), resources_root_acl_level=AclLevel.READ)
    ctx = RequestContext(user=UserIdentifier("account", "alice"), role=Role(Role.USER))

    acl = await manager.resolve("viking://resources/existing", ctx)
    assert acl_allows(acl, ctx, AclAction.READ)
    assert not acl_allows(acl, ctx, AclAction.WRITE)
    assert store.records["child"]["acl_inherited_grants"] == ["7:user:*"]
    assert store.upsert_count == 0

    store.records["root"]["acl_resources_root_level"] = "read"
    store.records["root"]["acl_direct_grants"] = ["1:user:*"]
    store.records["child"]["acl_resources_root_level"] = "read"
    store.records["child"]["acl_inherited_grants"] = ["1:user:*"]
    manager = AclManager(cast(Any, store), resources_root_acl_level=AclLevel.MANAGE)
    acl = await manager.resolve("viking://resources/existing", ctx)
    assert acl_allows(acl, ctx, AclAction.MANAGE)
    assert store.records["child"]["acl_inherited_grants"] == ["1:user:*"]
    assert store.upsert_count == 0


@pytest.mark.asyncio
async def test_resources_root_level_change_recomputes_without_root_record():
    store = _ContextStore(
        [
            {
                "id": "child",
                "uri": "viking://resources/existing",
                "acl_mode": "inherit",
                "acl_direct_grants": [],
                "acl_inherited_grants": ["1:user:*"],
                "acl_resources_root_level": "read",
            },
        ]
    )
    manager = AclManager(cast(Any, store), resources_root_acl_level=AclLevel.MANAGE)
    ctx = RequestContext(user=UserIdentifier("account", "alice"), role=Role(Role.USER))

    acl = await manager.resolve("viking://resources/existing", ctx)

    assert acl_allows(acl, ctx, AclAction.MANAGE)
    assert store.upsert_count == 0


@pytest.mark.asyncio
async def test_resources_root_level_change_preserves_restricted_boundary():
    store = _ContextStore(
        [
            {
                "id": "private",
                "uri": "viking://resources/private",
                "acl_mode": "restricted",
                "acl_direct_grants": ["1:user:bob"],
                "acl_inherited_grants": ["7:user:*"],
            },
            {
                "id": "child",
                "uri": "viking://resources/private/child",
                "acl_mode": "inherit",
                "acl_direct_grants": [],
                "acl_inherited_grants": ["1:user:bob"],
            },
        ]
    )
    manager = AclManager(cast(Any, store), resources_root_acl_level=AclLevel.READ)
    alice = RequestContext(user=UserIdentifier("account", "alice"), role=Role(Role.USER))
    bob = RequestContext(user=UserIdentifier("account", "bob"), role=Role(Role.USER))

    alice_acl = await manager.resolve("viking://resources/private/child", alice)
    bob_acl = await manager.resolve("viking://resources/private/child", bob)

    assert not acl_allows(alice_acl, alice, AclAction.READ)
    assert acl_allows(bob_acl, bob, AclAction.READ)


@pytest.mark.asyncio
async def test_resources_root_level_change_preserves_explicit_wildcard_grant():
    store = _ContextStore(
        [
            {
                "id": "parent",
                "uri": "viking://resources/team",
                "acl_mode": "inherit",
                "acl_direct_grants": ["7:user:*"],
                "acl_inherited_grants": ["7:user:*"],
            },
            {
                "id": "child",
                "uri": "viking://resources/team/child",
                "acl_mode": "inherit",
                "acl_direct_grants": [],
                "acl_inherited_grants": ["7:user:*"],
            },
        ]
    )
    manager = AclManager(cast(Any, store), resources_root_acl_level=AclLevel.READ)
    ctx = RequestContext(user=UserIdentifier("account", "alice"), role=Role(Role.USER))

    acl = await manager.resolve("viking://resources/team/child", ctx)

    assert acl_allows(acl, ctx, AclAction.MANAGE)


@pytest.mark.asyncio
async def test_default_manage_does_not_rewrite_legacy_root_records():
    store = _ContextStore(
        [
            {"id": "root", "uri": "viking://resources"},
            {
                "id": "child",
                "uri": "viking://resources/existing",
                "acl_mode": "inherit",
                "acl_direct_grants": [],
                "acl_inherited_grants": ["7:user:*"],
            },
        ]
    )
    manager = AclManager(cast(Any, store))
    ctx = RequestContext(user=UserIdentifier("account", "alice"), role=Role(Role.USER))

    acl = await manager.resolve("viking://resources/existing", ctx)

    assert acl_allows(acl, ctx, AclAction.MANAGE)
    assert store.upsert_count == 0


@pytest.mark.asyncio
async def test_materialized_records_store_resources_root_level():
    manager = AclManager(cast(Any, _EmptyContextStore()), resources_root_acl_level="write")
    ctx = RequestContext(user=UserIdentifier("account", "alice"), role=Role(Role.USER))

    records = await manager.materialize_context_records([{"uri": "viking://resources/new"}], ctx)

    assert records[0]["acl_resources_root_level"] == "write"
    assert records[0]["acl_inherited_grants"] == ["3:user:*"]
