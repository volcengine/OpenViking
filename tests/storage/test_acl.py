import pytest

from openviking.storage.acl import AclLevel, DirectAcl


@pytest.mark.parametrize(
    "raw_grants",
    [
        None,
        [],
        ["none"],
        ("none",),
    ],
)
@pytest.mark.parametrize("prefix", ["acl_direct", "acl_inherited"])
def test_context_fields_read_empty_grants(raw_grants, prefix):
    acl = DirectAcl.from_context_fields(
        {f"{prefix}_grants": raw_grants},
        prefix,
    )

    assert acl.empty
    assert acl.entries == ()
    assert acl.context_fields(prefix) == {f"{prefix}_grants": ["none"]}


@pytest.mark.parametrize("prefix", ["acl_direct", "acl_inherited"])
def test_context_fields_write_missing_grants_as_empty_sentinel(prefix):
    acl = DirectAcl.from_context_fields({}, prefix)

    assert acl.empty
    assert acl.context_fields(prefix) == {f"{prefix}_grants": ["none"]}


@pytest.mark.parametrize("prefix", ["acl_direct", "acl_inherited"])
def test_context_fields_preserve_valid_grants(prefix):
    raw_grants = ["3:user:alice", "1:group:readers"]
    acl = DirectAcl.from_context_fields(
        {f"{prefix}_grants": raw_grants},
        prefix,
    )

    assert {(entry.principal, entry.level) for entry in acl.entries} == {
        ("user:alice", AclLevel.WRITE),
        ("group:readers", AclLevel.READ),
    }
    assert set(acl.context_fields(prefix)[f"{prefix}_grants"]) == set(raw_grants)


@pytest.mark.parametrize(
    "raw_grants",
    [
        [""],
        ["", ""],
        ("", ""),
        ["", "3:user:alice"],
        ["none", "3:user:alice"],
        ["3:user:alice", "none"],
        ["none", "none"],
        ("none", "none"),
        ["none", ""],
        ["NONE"],
        [" none "],
        [" "],
        ["invalid"],
        ["", "invalid"],
        ["", 1],
    ],
)
@pytest.mark.parametrize("prefix", ["acl_direct", "acl_inherited"])
def test_context_fields_reject_invalid_grants(raw_grants, prefix):
    with pytest.raises(RuntimeError, match="Invalid ACL grant token"):
        DirectAcl.from_context_fields(
            {f"{prefix}_grants": raw_grants},
            prefix,
        )


@pytest.mark.parametrize("raw_grants", ["none", "3:user:alice"])
def test_context_fields_reject_nonlist_grants(raw_grants):
    with pytest.raises(RuntimeError, match="expected a list"):
        DirectAcl.from_context_fields(
            {"acl_direct_grants": raw_grants},
            "acl_direct",
        )
