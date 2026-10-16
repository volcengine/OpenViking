# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.session import session as session_module
from openviking.session.memory_policy import MemoryPolicy


@pytest.mark.parametrize("enable_working_memory", [None, False, True])
def test_global_enabled_agent_evolution_preserves_session_memory_types(enable_working_memory):
    policy = MemoryPolicy.from_dict(
        {
            "memory_types": ["profile", "events", "cases", "trajectories", "experiences"],
            "working_memory": (
                {"enabled": enable_working_memory} if enable_working_memory is not None else None
            ),
        }
    )

    effective = session_module._apply_agent_evolution_setting(
        policy,
        agent_evolution_enabled=True,
    )

    assert effective.memory_types == {
        "profile",
        "events",
        "cases",
        "trajectories",
        "experiences",
    }
    assert effective.self_enabled is True
    assert effective.peer_enabled is True
    assert effective.enable_working_memory is (enable_working_memory is True)


def test_disabled_agent_evolution_cannot_be_bypassed_by_session_policy():
    policy = MemoryPolicy.from_dict(
        {"memory_types": ["profile", "cases", "trajectories", "experiences"]}
    )

    effective = session_module._apply_agent_evolution_setting(
        policy,
        agent_evolution_enabled=False,
    )

    assert effective.memory_types == {"profile"}


def test_disabled_agent_evolution_removes_agent_memory_types_from_default_policy():
    effective = session_module._apply_agent_evolution_setting(
        MemoryPolicy.default(),
        agent_evolution_enabled=False,
    )

    assert effective.memory_types is not None
    assert "profile" in effective.memory_types
    assert "cases" not in effective.memory_types
    assert "trajectories" not in effective.memory_types
    assert "experiences" not in effective.memory_types


def test_agent_memory_skip_reason_requires_experiences():
    assert (
        session_module._agent_memory_skip_reason(
            agent_evolution_enabled=True,
            effective_memory_types={"cases", "trajectories"},
        )
        == "memory_types_filtered"
    )
    assert (
        session_module._agent_memory_skip_reason(
            agent_evolution_enabled=True,
            effective_memory_types={"cases", "trajectories", "experiences"},
        )
        is None
    )
