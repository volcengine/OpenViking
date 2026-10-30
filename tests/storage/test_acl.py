from copy import deepcopy

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.acl import DirectAcl
from openviking_cli.session.user_id import UserIdentifier
from tests.storage.test_vector_transfer import _RealAclMemoryTransferBackend, _record


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


@pytest.mark.parametrize("principal", ["user:bob", "group:engineering"])
async def test_remove_principal_cleans_all_pages_and_retries_partial_writes(principal):
    records = [
        _record(
            str(index),
            f"viking://resources/target/{index // 3}.md",
            level=index % 3,
            acl_mode="restricted" if index % 2 else "inherit",
            acl_direct_grants=[f"{(1, 3, 7)[index % 3]}:{principal}", "7:user:alice"],
            acl_inherited_grants=["", f"1:{principal}", f"3:{principal}-other"],
        )
        for index in range(503)
    ]
    untouched = [
        {**records[0], "id": "other-account", "account_id": "another"},
        {**records[0], "id": "private", "uri": "viking://user/alice/resources/private.md"},
    ]
    backend = _RealAclMemoryTransferBackend(deepcopy(records + untouched))
    backend.get_strict = backend.get
    ctx = RequestContext(user=UserIdentifier("acct", "admin"), role=Role.ROOT)
    # Fail after the first page has already been changed. Retrying must find
    # every remaining record without skipping shifted scroll offsets.
    backend.fail_upsert_at = 501
    with pytest.raises(RuntimeError, match="injected vector write failure"):
        await backend.acl_manager.remove_principal(principal, ctx)
    backend.fail_upsert_at = None
    await backend.acl_manager.remove_principal(principal, ctx)
    await backend.acl_manager.remove_principal(principal, ctx)
    for original in records:
        assert backend.records[original["id"]] == {
            **original,
            "acl_direct_grants": ["7:user:alice"],
            "acl_inherited_grants": [f"3:{principal}-other"],
        }
    for original in untouched:
        assert backend.records[original["id"]] == original


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
