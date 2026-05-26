# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.session.memory_policy import MemoryPolicy
from openviking_cli.exceptions import InvalidArgumentError


def test_memory_policy_defaults_to_self_only():
    policy = MemoryPolicy.from_dict(None)

    assert policy.self_memory.enabled is True
    assert policy.self_memory.types is None
    assert policy.peer_memory.enabled is False
    assert policy.peer_memory.types is None


def test_memory_policy_types_filter_registered_types():
    policy = MemoryPolicy.from_dict(
        {
            "self": {"types": ["profile", "preferences"]},
            "peer": {"enabled": True, "types": ["events"]},
        }
    )

    registered = {"profile", "preferences", "events", "tools"}
    assert policy.self_memory.allowed_types(registered) == {"profile", "preferences"}
    assert policy.peer_memory.allowed_types(registered) == {"events"}


def test_memory_policy_peer_defaults_to_peer_memory_types():
    policy = MemoryPolicy.from_dict({"peer": {"enabled": True}})

    registered = {"profile", "preferences", "entities", "events", "tools", "skills"}

    assert policy.self_allowed_types(registered) == registered
    assert policy.peer_allowed_types(registered) == {
        "profile",
        "preferences",
        "entities",
        "events",
    }


def test_memory_policy_commit_replaces_session_policy():
    session_policy = {"peer": {"enabled": True, "types": ["profile"]}}
    commit_policy = {"self": {"enabled": False}, "peer": {"enabled": False}}

    policy = MemoryPolicy.merge(session_policy=session_policy, commit_policy=commit_policy)

    assert policy.self_memory.enabled is False
    assert policy.peer_memory.enabled is False


def test_memory_policy_rejects_unknown_types():
    policy = MemoryPolicy.from_dict({"peer": {"enabled": True, "types": ["unknown"]}})

    try:
        policy.validate_types({"profile"})
    except InvalidArgumentError as exc:
        assert "unknown" in str(exc)
    else:
        raise AssertionError("expected InvalidArgumentError")


def test_memory_policy_rejects_self_only_types_for_peer():
    policy = MemoryPolicy.from_dict({"peer": {"enabled": True, "types": ["tools"]}})

    try:
        policy.validate_types({"profile", "tools"})
    except InvalidArgumentError as exc:
        assert "tools" in str(exc)
    else:
        raise AssertionError("expected InvalidArgumentError")
