# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openviking.message import Message, TextPart
from openviking.session.train import Case, Rollout, Rubric
from openviking.session.train.adapters.trajectory_analyzer import (
    LegacyTrajectoryAnalyzerContext,
    LegacyTrajectoryRolloutAnalyzer,
)


class FakeCompressor:
    def __init__(self):
        self.calls = []

    async def extract_agent_memories(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "contexts": [
                SimpleNamespace(
                    uri="viking://user/u/memories/trajectories/task_2026.md",
                    category="memory_write",
                ),
                SimpleNamespace(
                    uri="viking://user/u/memories/experiences/ignored.md",
                    category="memory_write",
                ),
            ],
            "session_skills": [],
        }


class FakeVikingFS:
    async def read_file(self, uri, ctx=None):
        assert uri == "viking://user/u/memories/trajectories/task_2026.md"
        return (
            "# task\nbody\n\n<!-- MEMORY_FIELDS\n"
            '{"memory_type":"trajectories","trajectory_name":"task","outcome":"success",'
            '"retrieval_anchor":"Stage: final"}\n-->'
        )


def _rollout() -> Rollout:
    return Rollout(
        case=Case(
            name="case",
            task_signature="task",
            input={},
            rubric=Rubric(name="r", description="d", criteria=[]),
        ),
        messages=[Message(id="m", role="user", parts=[TextPart(text="hello")])],
        policy_snapshot_id="snapshot",
    )


@pytest.mark.asyncio
async def test_legacy_trajectory_rollout_analyzer_restricts_to_trajectory_phase():
    compressor = FakeCompressor()
    analyzer = LegacyTrajectoryRolloutAnalyzer(compressor=compressor, viking_fs=FakeVikingFS())
    context = LegacyTrajectoryAnalyzerContext(request_context=SimpleNamespace())

    analysis = await analyzer.analyze(_rollout(), context)

    assert compressor.calls[0]["allowed_memory_types"] == {"trajectories"}
    assert compressor.calls[0]["include_session_skills"] is False
    assert len(analysis.trajectories) == 1
    traj = analysis.trajectories[0]
    assert traj.name == "task"
    assert traj.outcome == "success"
    assert traj.retrieval_anchor == "Stage: final"
    assert analysis.evaluation.passed is True
    assert analysis.metadata["policy_snapshot_id"] == "snapshot"
