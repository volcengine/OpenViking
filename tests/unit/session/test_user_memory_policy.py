# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.session import session as session_module
from openviking.session.memory_policy import MemoryPolicy


def test_enabled_agent_evolution_preserves_session_memory_types():
    policy = MemoryPolicy.from_dict({"memory_types": ["profile", "events", "trajectories"]})

    effective = session_module._apply_agent_evolution_setting(
        policy,
        agent_evolution_enabled=True,
    )

    assert effective.memory_types == {"profile", "events", "trajectories"}
    assert effective.self_enabled is True
    assert effective.peer_enabled is True
    assert effective.working_memory_enabled is True


def test_disabled_agent_evolution_cannot_be_bypassed_by_session_policy():
    policy = MemoryPolicy.from_dict({"memory_types": ["profile", "trajectories", "experiences"]})

    effective = session_module._apply_agent_evolution_setting(
        policy,
        agent_evolution_enabled=False,
    )

    assert effective.memory_types == {"profile"}


def test_disabled_agent_evolution_removes_execution_types_from_default_policy():
    effective = session_module._apply_agent_evolution_setting(
        MemoryPolicy.default(),
        agent_evolution_enabled=False,
    )

    assert effective.memory_types is not None
    assert "profile" in effective.memory_types
    assert "trajectories" not in effective.memory_types
    assert "experiences" not in effective.memory_types


def test_agent_memory_skip_reason_requires_case_and_trajectory_types():
    assert (
        session_module._agent_memory_skip_reason(
            agent_evolution_enabled=True,
            effective_memory_types={"cases", "experiences"},
        )
        == "memory_types_filtered"
    )
    assert (
        session_module._agent_memory_skip_reason(
            agent_evolution_enabled=True,
            effective_memory_types={"cases", "trajectories"},
        )
        is None
    )
