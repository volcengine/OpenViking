# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.session import session as session_module
from openviking.session.memory_policy import MemoryPolicy


def test_user_memory_types_narrow_session_policy():
    policy = MemoryPolicy.from_dict({"memory_types": ["profile", "events", "trajectories"]})

    effective = session_module._apply_user_memory_settings(
        policy,
        allowed_memory_types={"profile", "trajectories", "experiences"},
        agent_evolution_enabled=True,
    )

    assert effective.memory_types == {"profile", "trajectories"}
    assert effective.self_enabled is True
    assert effective.peer_enabled is True
    assert effective.working_memory_enabled is True


def test_disabled_agent_evolution_cannot_be_bypassed_by_session_policy():
    policy = MemoryPolicy.from_dict({"memory_types": ["profile", "trajectories", "experiences"]})

    effective = session_module._apply_user_memory_settings(
        policy,
        allowed_memory_types={"profile", "trajectories", "experiences"},
        agent_evolution_enabled=False,
    )

    assert effective.memory_types == {"profile"}


def test_disabled_agent_evolution_removes_execution_types_from_default_policy():
    effective = session_module._apply_user_memory_settings(
        MemoryPolicy.default(),
        allowed_memory_types={"profile", "trajectories", "experiences"},
        agent_evolution_enabled=False,
    )

    assert effective.memory_types == {"profile"}
