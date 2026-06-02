# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.session.memory_policy import MemoryPolicy


def test_memory_policy_defaults_to_self_only():
    policy = MemoryPolicy.from_dict(None)

    assert policy.self_enabled is True
    assert policy.peer_enabled is False


def test_memory_policy_uses_enabled_switches_only():
    policy = MemoryPolicy.from_dict(
        {
            "self": {"enabled": False, "types": ["profile"]},
            "peer": {"enabled": True, "types": ["events"]},
        }
    )

    assert policy.self_enabled is False
    assert policy.peer_enabled is True
    assert policy.to_dict() == {
        "self": {"enabled": False},
        "peer": {"enabled": True},
    }


def test_memory_policy_commit_replaces_session_policy():
    session_policy = {"peer": {"enabled": True, "types": ["profile"]}}
    commit_policy = {"self": {"enabled": False}, "peer": {"enabled": False}}

    policy = MemoryPolicy.merge(session_policy=session_policy, commit_policy=commit_policy)

    assert policy.self_enabled is False
    assert policy.peer_enabled is False
