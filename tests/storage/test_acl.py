import pytest

from openviking.storage.acl import AclLevel, DirectAcl


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
