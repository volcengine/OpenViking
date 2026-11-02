from copy import deepcopy
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.acl import (
    ACL_CONTEXT_FIELDS,
    AclAction,
    AclLevel,
    AclManager,
    AclMode,
    DirectAcl,
    EffectiveAcl,
)
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier


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


class _AclUpdateStore:
    """Store that rejects missing records and can fail after a partial write."""

    def __init__(self, records):
        self.records = {record["id"]: deepcopy(record) for record in records}
        self.calls = []
        self.first_update = None
        self.unavailable_ids = set()

    async def _update_acl_many_raw(self, payloads, *, ctx):
        self.calls.append(deepcopy(payloads))
        if len(self.calls) == 1 and self.first_update is not None:
            self.first_update(payloads)
        for payload in payloads:
            record_id = payload["id"]
            if record_id not in self.records:
                raise ValueError(f"Record not found: {record_id}")
            if record_id in self.unavailable_ids:
                raise RuntimeError(f"Record update failed: {record_id}")
        for payload in payloads:
            self.records[payload["id"]].update(deepcopy(payload))
        return [payload["id"] for payload in payloads]


def _acl_record(record_id, uri, *, level=2, direct=(), inherited=(), mode="inherit"):
    return {
        "id": record_id,
        "account_id": "default",
        "uri": uri,
        "level": level,
        "vector": [0.1, 0.2],
        "content": f"content for {record_id}",
        "acl_mode": mode,
        "acl_direct_grants": list(direct),
        "acl_inherited_grants": list(inherited),
    }


def _acl_manager(records):
    store = _AclUpdateStore(records)
    manager = AclManager(store)
    # Keep the tests focused on the read snapshot / patch / compensation contract.
    manager._subtree_records = AsyncMock(
        side_effect=lambda *_: deepcopy(list(store.records.values()))
    )
    manager.resolve = AsyncMock(
        return_value=EffectiveAcl.from_permissions(
            DirectAcl.from_entries([{"principal": "user:*", "level": "manage"}]),
            DirectAcl(),
        )
    )
    ctx = RequestContext(user=UserIdentifier("default", "admin"), role=Role.ADMIN)
    return manager, store, ctx


@pytest.mark.asyncio
async def test_set_acl_patches_all_levels_and_preserves_restricted_boundaries():
    root = "viking://resources/project"
    records = [
        _acl_record("root-l0", root, level=0),
        _acl_record("root-l1", root, level=1),
        _acl_record("open", f"{root}/open.md", direct=["1:group:readers"]),
        _acl_record(
            "private-l0", f"{root}/private", level=0, direct=["7:user:bob"], mode="restricted"
        ),
        _acl_record(
            "private-l1", f"{root}/private", level=1, direct=["7:user:bob"], mode="restricted"
        ),
        _acl_record("private-child", f"{root}/private/child.md"),
    ]
    manager, store, ctx = _acl_manager(records)

    effective = await manager.set_acl(
        root, [{"principal": "user:alice", "level": "manage"}], ctx, acl_mode=AclMode.RESTRICTED
    )

    assert effective.permissions.principals_for(AclAction.MANAGE) == {"user:alice"}
    assert len(store.calls) == 1
    assert len(store.calls[0]) == len(records)
    for payload in store.calls[0]:
        assert set(payload) == {"id", "account_id", *ACL_CONTEXT_FIELDS}
    for record in records:
        current = store.records[record["id"]]
        assert {key: value for key, value in current.items() if key not in ACL_CONTEXT_FIELDS} == {
            key: value for key, value in record.items() if key not in ACL_CONTEXT_FIELDS
        }
    for record_id in ("root-l0", "root-l1"):
        assert store.records[record_id]["acl_direct_grants"] == ["7:user:alice"]
        assert store.records[record_id]["acl_mode"] == "restricted"
    assert store.records["open"]["acl_inherited_grants"] == ["7:user:alice"]
    assert store.records["open"]["acl_direct_grants"] == ["1:group:readers"]
    for record_id in ("private-l0", "private-l1"):
        assert store.records[record_id]["acl_inherited_grants"] == ["7:user:alice"]
        assert store.records[record_id]["acl_direct_grants"] == ["7:user:bob"]
        assert store.records[record_id]["acl_mode"] == "restricted"
    assert store.records["private-child"]["acl_inherited_grants"] == ["7:user:bob"]


@pytest.mark.asyncio
async def test_set_empty_restricted_acl_explicitly_clears_descendant_grants():
    root = "viking://resources/project"
    manager, store, ctx = _acl_manager(
        [
            _acl_record("root", root, direct=["7:user:alice"], mode="restricted"),
            _acl_record("child", f"{root}/child.md", inherited=["7:user:alice"]),
        ]
    )

    await manager.set_acl(root, [], ctx, acl_mode=AclMode.RESTRICTED)

    assert store.records["root"]["acl_direct_grants"] == []
    assert store.records["child"]["acl_inherited_grants"] == []
    assert store.records["child"]["acl_mode"] == "inherit"
    assert store.calls[0][1]["acl_direct_grants"] == []
    assert store.calls[0][1]["acl_inherited_grants"] == []


@pytest.mark.asyncio
async def test_acl_partial_failure_restores_only_acl_fields():
    root = "viking://resources/project"
    records = [
        _acl_record("root", root, direct=["7:user:alice"], mode="restricted"),
        _acl_record("child", f"{root}/child.md", inherited=["7:user:alice"]),
    ]
    manager, store, ctx = _acl_manager(records)
    failure = RuntimeError("second batch failed")

    def fail_after_partial_write(payloads):
        store.records["root"].update(payloads[0])
        store.records["root"].update(content="concurrent content", vector=[0.8, 0.9])
        raise failure

    store.first_update = fail_after_partial_write

    with pytest.raises(RuntimeError) as caught:
        await manager.set_acl(root, [], ctx, acl_mode=AclMode.RESTRICTED)

    assert caught.value is failure
    assert len(store.calls) == 2  # One forward batch and one compensation batch.
    for record in records:
        assert {key: store.records[record["id"]][key] for key in ACL_CONTEXT_FIELDS} == {
            key: record[key] for key in ACL_CONTEXT_FIELDS
        }
    assert store.records["root"]["content"] == "concurrent content"
    assert store.records["root"]["vector"] == [0.8, 0.9]
    assert all(
        set(payload) == {"id", "account_id", *ACL_CONTEXT_FIELDS}
        for batch in store.calls
        for payload in batch
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("delete_record", [False, True])
async def test_acl_rollback_failure_recovers_other_records_and_preserves_original_error(
    delete_record, monkeypatch
):
    root = "viking://resources/project"
    records = [
        _acl_record("root", root, direct=["7:user:alice"], mode="restricted"),
        _acl_record("child", f"{root}/child.md", inherited=["7:user:alice"]),
    ]
    manager, store, ctx = _acl_manager(records)
    failure = RuntimeError("forward update interrupted")
    logger = Mock()
    monkeypatch.setattr("openviking.storage.acl.logger", logger)

    def fail_after_partial_write(payloads):
        for payload in payloads:
            store.records[payload["id"]].update(payload)
        store.records["child"]["content"] = "concurrent child update"
        if delete_record:
            del store.records["root"]
        else:
            store.unavailable_ids.add("root")
        raise failure

    store.first_update = fail_after_partial_write

    with pytest.raises(RuntimeError) as caught:
        await manager.set_acl(root, [], ctx, acl_mode=AclMode.RESTRICTED)

    assert caught.value is failure
    assert len(store.calls) == 4  # Forward, batch rollback, and both individual retries.
    assert {key: store.records["child"][key] for key in ACL_CONTEXT_FIELDS} == {
        key: records[1][key] for key in ACL_CONTEXT_FIELDS
    }
    assert store.records["child"]["content"] == "concurrent child update"
    if delete_record:
        assert "root" not in store.records
    logger.warning.assert_called_once_with(
        "Batch ACL rollback failed for %s; retrying records individually", root, exc_info=True
    )
    logger.error.assert_called_once_with(
        "Failed to restore context ACL record id=%s account_id=%s",
        "root",
        "default",
        exc_info=True,
    )


@pytest.mark.asyncio
async def test_set_acl_requires_indexed_target_without_inserting_record():
    manager, store, ctx = _acl_manager([])

    with pytest.raises(InvalidArgumentError, match="index it first"):
        await manager.set_acl("viking://resources/missing", [], ctx)

    assert store.calls == []
    assert store.records == {}


@pytest.mark.asyncio
async def test_refresh_context_subtree_updates_existing_records_without_changing_direct_acl():
    root = "viking://resources/project"
    manager, store, ctx = _acl_manager(
        [
            _acl_record("root", root, direct=["7:user:alice"], mode="restricted"),
            _acl_record("child", f"{root}/child.md", inherited=["7:user:outdated"]),
        ]
    )

    await manager.refresh_context_subtree(root, ctx)

    assert len(store.calls) == 1
    assert store.records["root"]["acl_direct_grants"] == ["7:user:alice"]
    assert store.records["child"]["acl_inherited_grants"] == ["7:user:alice"]
