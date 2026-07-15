# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openviking.message import Message, TextPart
from openviking.session.memory.dataclass import ResolvedOperation, ResolvedOperations
from openviking.session.train import (
    Case,
    CriterionResult,
    Rollout,
    Rubric,
    RubricEvaluation,
)
from openviking.session.train.components.trajectory_analyzer import (
    TrajectoryAnalyzerContext,
    TrajectoryRolloutAnalyzer,
)


class FakeExtractLoop:
    created = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self._transaction_handle = None
        FakeExtractLoop.created.append(self)

    async def run(self):
        return (
            ResolvedOperations(
                upsert_operations=[
                    ResolvedOperation(
                        old_memory_file_content=None,
                        memory_fields={
                            "trajectory_name": "task",
                            "outcome": "success",
                            "retrieval_anchor": "Stage: final",
                            "content": "# task\nbody",
                        },
                        memory_type="trajectories",
                        uris=["viking://user/u/memories/trajectories/task_20260607120000.md"],
                        page_id=100,
                    )
                ],
                delete_file_contents=[],
                errors=[],
                resolved_links=[],
            ),
            [],
        )


class FakeVikingFS:
    agfs = None

    def __init__(self):
        self.files = {}
        self.writes = []

    async def read_file(self, uri, ctx=None):
        return self.files[uri]

    async def write_file(self, uri, content, ctx=None):
        self.files[uri] = content
        self.writes.append((uri, content, ctx))


class FakeRolloutEvaluator:
    def __init__(self):
        self.calls = []

    async def evaluate(self, rollout, context):
        self.calls.append((rollout, context))
        return RubricEvaluation(
            passed=False,
            score=0.25,
            criterion_results=[
                CriterionResult(
                    criterion_name="tau2_reward",
                    passed=False,
                    score=0.0,
                    feedback=["reward was zero"],
                    evidence=["missing confirmation"],
                )
            ],
            feedback=["task failed"],
            metadata={"source": "fake"},
        )


def _rollout() -> Rollout:
    return Rollout(
        case=Case(
            name="case",
            task_signature="task",
            input={},
            rubric=Rubric(name="r", description="d", criteria=[]),
        ),
        messages=[
            Message(
                id="m",
                role="user",
                parts=[TextPart(text="hello")],
                created_at="2026-06-07T12:00:00",
            )
        ],
        policy_snapshot_id="snapshot",
    )


@pytest.mark.asyncio
async def test_trajectory_rollout_analyzer_extracts_and_persists_trajectory(monkeypatch):
    from openviking.session.train.components import trajectory_analyzer as module

    FakeExtractLoop.created.clear()
    fs = FakeVikingFS()
    monkeypatch.setattr(module, "ExtractLoop", FakeExtractLoop)
    monkeypatch.setattr(module, "get_viking_fs", lambda: fs)

    analyzer = TrajectoryRolloutAnalyzer(viking_fs=fs, vlm=SimpleNamespace(model="fake"))
    context = TrajectoryAnalyzerContext(
        request_context=SimpleNamespace(
            user=SimpleNamespace(account_id="default", user_id="u"),
            account_id="default",
        ),
        source_archive_uri="viking://user/u/sessions/s1/history/archive_001",
    )

    analysis = await analyzer.analyze(_rollout(), context)

    assert FakeExtractLoop.created
    created_loop = FakeExtractLoop.created[0]
    assert created_loop._transaction_handle is None
    provider = created_loop.kwargs["context_provider"]
    assert provider._transaction_handle is None
    assert [
        schema.memory_type for schema in provider.get_memory_schemas(context.request_context)
    ] == ["trajectories"]
    assert len(fs.writes) == 1
    assert fs.writes[0][0] == "viking://user/u/memories/trajectories/task_20260607120000.md"
    assert '"case_name": "case"' in fs.writes[0][1]
    assert (
        '"source_archive_uri": "viking://user/u/sessions/s1/history/archive_001"' in fs.writes[0][1]
    )
    assert '"source_session_id"' not in fs.writes[0][1]
    assert '"source_messages_uri"' not in fs.writes[0][1]
    assert '"source_task_id"' not in fs.writes[0][1]
    assert '"source_trace_id"' not in fs.writes[0][1]
    assert len(analysis.trajectories) == 1
    traj = analysis.trajectories[0]
    assert traj.name == "task"
    assert traj.outcome == "success"
    assert traj.retrieval_anchor == "Stage: final"
    assert traj.metadata["case_name"] == "case"
    assert analysis.evaluation.passed is True
    assert analysis.metadata["policy_snapshot_id"] == "snapshot"


@pytest.mark.asyncio
async def test_trajectory_rollout_analyzer_evaluates_before_extracting_trajectory(monkeypatch):
    from openviking.session.train.components import trajectory_analyzer as module

    FakeExtractLoop.created.clear()
    fs = FakeVikingFS()
    evaluator = FakeRolloutEvaluator()
    evaluator_context = {"benchmark": "tau2"}
    monkeypatch.setattr(module, "ExtractLoop", FakeExtractLoop)
    monkeypatch.setattr(module, "get_viking_fs", lambda: fs)

    analyzer = TrajectoryRolloutAnalyzer(
        viking_fs=fs,
        vlm=SimpleNamespace(model="fake"),
        evaluator=evaluator,
    )
    context = TrajectoryAnalyzerContext(
        request_context=SimpleNamespace(
            user=SimpleNamespace(account_id="default", user_id="u"),
            account_id="default",
        ),
        evaluator_context=evaluator_context,
    )

    rollout = _rollout()
    analysis = await analyzer.analyze(rollout, context)

    assert evaluator.calls == [(rollout, evaluator_context)]
    assert analysis.evaluation.score == 0.25
    assert analysis.evaluation.metadata == {"source": "fake"}
    created_loop = FakeExtractLoop.created[0]
    provider = created_loop.kwargs["context_provider"]
    assert len(provider.messages) == 2
    assert provider.messages[0] is rollout.messages[0]
    feedback_message = provider.messages[1]
    assert feedback_message.role == "user"
    assert "[Rollout Evaluation]" in feedback_message.content
    assert "score: 0.25" in feedback_message.content
    assert "task failed" in feedback_message.content
    assert "missing confirmation" in feedback_message.content
    assert analysis.metadata["extraction_message_count"] == 2
