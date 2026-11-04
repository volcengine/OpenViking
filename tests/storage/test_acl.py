import pytest

from openviking.storage.acl import AclLevel, DirectAcl


@pytest.mark.parametrize(
    "record",
    [
        {},
        {"acl_direct_grants": None},
        {"acl_direct_grants": []},
        {"acl_direct_grants": ["none"]},
    ],
)
def test_context_fields_roundtrip_empty_grants(record):
    acl = DirectAcl.from_context_fields(record, "acl_direct")

    assert acl.empty
    assert acl.context_fields("acl_direct") == {"acl_direct_grants": ["none"]}


def test_context_fields_preserve_valid_grants():
    raw_grants = ["3:user:alice", "1:group:readers"]
    acl = DirectAcl.from_context_fields(
        {"acl_inherited_grants": raw_grants},
        "acl_inherited",
    )

    assert {(entry.principal, entry.level) for entry in acl.entries} == {
        ("user:alice", AclLevel.WRITE),
        ("group:readers", AclLevel.READ),
    }
    assert set(acl.context_fields("acl_inherited")["acl_inherited_grants"]) == set(raw_grants)


@pytest.mark.parametrize(
    "raw_grants",
    [[""], ["none", "3:user:alice"], ["none", "none"], [1]],
)
def test_context_fields_reject_invalid_grants(raw_grants):
    with pytest.raises(RuntimeError, match="Invalid ACL grant token"):
        DirectAcl.from_context_fields(
            {"acl_direct_grants": raw_grants},
            "acl_direct",
        )


def test_context_fields_reject_nonlist_grants():
    with pytest.raises(RuntimeError, match="expected a list"):
        DirectAcl.from_context_fields(
            {"acl_direct_grants": "none"},
            "acl_direct",
        )
