# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

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
    _trajectory_operation_validation_issues,
    _trajectory_validation_issues,
)


def _valid_trajectory_content() -> str:
    return """# task
- Outcome: failure
- Domain: service operation
- User Goal: complete an operation and verify its result
- Injected Experiences:
  - Loaded: none
  - Helpful: none
  - Misleading: none
  - Insufficient: none

## Key Steps

### Step 1
- Boundary: operation_result_interpretation
- Trigger: the requested operation returned a tool result
- Observed facts: tool result reported a timeout
- Decision: treated the timeout as success
- Decision basis: no verification result was obtained
- Action: stopped without verification
- Result: completion remained unverified
- Evidence: operation tool result reported timeout

### Step 2
- Boundary: final_response
- Trigger: the agent prepared the final response
- Observed facts: completion remained unverified
- Decision: report completion
- Decision basis: assumed the earlier operation succeeded
- Action: sent operation complete
- Result: user received an unsupported completion claim
- Evidence: final assistant message said operation complete

## Evaluation
- Required outcome: failed; completion was not verified
- Failed requirements: completion verification and grounded final response
- External feedback: none
- Unexplained differences: none

## Result
- Completion was reported without verification"""


def _valid_trajectory_fields(**overrides):
    fields = {
        "trajectory_name": "task",
        "outcome": "failure",
        "retrieval_anchor": (
            "Stage: final_response; Boundary: completion verification; "
            "Capability: verify; Target: requested operation; Outcome: failure"
        ),
        "experience_effects": '{"positive_ids":[],"negative_ids":[],"weak_ids":[]}',
        "content": _valid_trajectory_content(),
    }
    fields.update(overrides)
    return fields


def test_trajectory_prompt_requires_normalized_key_steps():
    prompt_path = (
        Path(__file__).parents[3] / "openviking/prompts/templates/memory/trajectories.yaml"
    )
    prompt = yaml.safe_load(prompt_path.read_text(encoding="utf-8"))["fields"][-1]["description"]

    for label in (
        "## Key Steps",
        "### Step <positive integer>",
        "- Boundary:",
        "- Trigger:",
        "- Observed facts:",
        "- Decision:",
        "- Decision basis:",
        "- Action:",
        "- Result:",
        "- Evidence:",
        "## Evaluation",
        "- Required outcome:",
        "- Failed requirements:",
        "- External feedback:",
        "- Unexplained differences:",
        "## Result",
    ):
        assert label in prompt

    for legacy_heading in (
        "- Timeline:",
        "- Outcome Checks:",
        "- Correct Work To Preserve:",
        "- Observed Problem:",
        "- Evidence References:",
        "- Raw Evidence:",
    ):
        assert legacy_heading not in prompt


def test_trajectory_validation_enforces_complete_schema_and_grounded_language():
    assert _trajectory_operation_validation_issues("task", _valid_trajectory_fields()) == []

    missing = _valid_trajectory_fields(content="# task\n- Outcome: failure")
    issues = _trajectory_operation_validation_issues("task", missing)
    assert any(
        issue.reason == "trajectory content is missing required sections" for issue in issues
    )

    invalid_effects = _valid_trajectory_fields(experience_effects="not-json")
    issues = _trajectory_operation_validation_issues("task", invalid_effects)
    assert any(
        issue.reason == "trajectory experience_effects is not valid JSON" for issue in issues
    )

    unsupported = _valid_trajectory_fields(
        content=re.sub(
            r"(?m)^- Evidence:.*$",
            "- Evidence: none",
            _valid_trajectory_content(),
        ),
    )
    issues = _trajectory_operation_validation_issues("task", unsupported)
    assert any(
        issue.reason == "trajectory material failure claim lacks direct evidence"
        for issue in issues
    )

    mismatched_outcome = _valid_trajectory_fields(outcome="success")
    issues = _trajectory_operation_validation_issues("task", mismatched_outcome)
    assert any(
        issue.reason == "trajectory outcome disagrees with content outcome" for issue in issues
    )


def test_trajectory_validation_canonicalizes_label_ordered_comma_anchor():
    operation = ResolvedOperation(
        old_memory_file_content=None,
        memory_fields=_valid_trajectory_fields(
            retrieval_anchor=(
                "Stage: repair, Boundary: source warnings, Capability: validate sources, "
                "Target: structured analysis, Outcome: failure"
            )
        ),
        memory_type="trajectories",
        uris=["viking://user/u/memories/trajectories/task.md"],
        page_id=100,
    )
    operations = ResolvedOperations(
        upsert_operations=[operation],
        delete_file_contents=[],
        errors=[],
        resolved_links=[],
    )

    issues = _trajectory_validation_issues(operations)

    assert issues == []
    assert operation.memory_fields["retrieval_anchor"] == (
        "Stage: repair; Boundary: source warnings; Capability: validate sources; "
        "Target: structured analysis; Outcome: failure."
    )


def test_trajectory_validation_requires_explicit_direct_external_evidence_source():
    content = _valid_trajectory_content().replace(
        "External feedback: none",
        "External feedback: independent observer confirmed the missing result",
    )
    fields = _valid_trajectory_fields(content=content)

    unsupported = _trajectory_operation_validation_issues("task", fields)
    supported = _trajectory_operation_validation_issues(
        "task",
        fields,
        evidence_sources={
            "direct_available": True,
            "items": [{"direct": True, "source": "independent_observer"}],
        },
    )

    assert any(
        issue.reason == "trajectory claims direct external evidence when none was supplied"
        for issue in unsupported
    )
    assert not any(
        issue.reason == "trajectory claims direct external evidence when none was supplied"
        for issue in supported
    )


def test_trajectory_validation_allows_domain_language_that_can_also_be_control_plane_language():
    content = _valid_trajectory_content().replace(
        "treated the timeout as success",
        "computed the requested evaluation reward from the observed timeout",
    )

    issues = _trajectory_operation_validation_issues(
        "task",
        _valid_trajectory_fields(content=content),
    )

    assert issues == []


def test_trajectory_validation_requires_every_key_step_field():
    content = _valid_trajectory_content().replace(
        "- Decision basis: no verification result was obtained\n",
        "",
        1,
    )

    issues = _trajectory_operation_validation_issues(
        "task",
        _valid_trajectory_fields(content=content),
    )

    assert any(
        issue.reason == "trajectory key steps are missing required fields" for issue in issues
    )


def test_trajectory_validation_rejects_duplicate_key_step_fields():
    content = _valid_trajectory_content().replace(
        "- Decision: treated the timeout as success\n",
        "- Decision: treated the timeout as success\n- Decision: stop processing\n",
        1,
    )

    issues = _trajectory_operation_validation_issues(
        "task",
        _valid_trajectory_fields(content=content),
    )

    assert any(issue.reason == "trajectory key steps repeat required fields" for issue in issues)


def test_trajectory_validation_requires_key_step_field_order():
    content = _valid_trajectory_content().replace(
        "- Decision: treated the timeout as success\n"
        "- Decision basis: no verification result was obtained",
        "- Decision basis: no verification result was obtained\n"
        "- Decision: treated the timeout as success",
        1,
    )

    issues = _trajectory_operation_validation_issues(
        "task",
        _valid_trajectory_fields(content=content),
    )

    assert any(issue.reason == "trajectory key step fields are out of order" for issue in issues)


def test_trajectory_validation_rejects_unexpected_key_step_fields():
    content = _valid_trajectory_content().replace(
        "- Evidence: operation tool result reported timeout",
        "- Root cause: the tool timed out\n- Evidence: operation tool result reported timeout",
        1,
    )

    issues = _trajectory_operation_validation_issues(
        "task",
        _valid_trajectory_fields(content=content),
    )

    assert any(issue.reason == "trajectory key steps contain unexpected fields" for issue in issues)


def test_trajectory_validation_requires_consecutive_key_step_numbers():
    content = _valid_trajectory_content().replace("### Step 2", "### Step 3")

    issues = _trajectory_operation_validation_issues(
        "task",
        _valid_trajectory_fields(content=content),
    )

    assert any(
        issue.reason == "trajectory key step numbers are not consecutive" for issue in issues
    )


def test_trajectory_validation_rejects_removed_diagnostic_sections():
    content = _valid_trajectory_content() + "\n\n- Observed Problem:\n  - Failure type: unknown"

    issues = _trajectory_operation_validation_issues(
        "task",
        _valid_trajectory_fields(content=content),
    )

    assert any(
        issue.reason == "trajectory content contains removed diagnostic sections"
        for issue in issues
    )


def test_trajectory_validation_rejects_unexpected_top_level_sections():
    content = _valid_trajectory_content() + "\n\n## Advice\n- Retry the operation"

    issues = _trajectory_operation_validation_issues(
        "task",
        _valid_trajectory_fields(content=content),
    )

    assert any(
        issue.reason == "trajectory content contains unexpected top-level sections"
        for issue in issues
    )


def test_trajectory_validation_requires_complete_evaluation_fields():
    content = _valid_trajectory_content().replace("- External feedback: none\n", "")

    issues = _trajectory_operation_validation_issues(
        "task",
        _valid_trajectory_fields(content=content),
    )

    assert any(
        issue.reason == "trajectory evaluation is missing required fields" for issue in issues
    )


def test_trajectory_validation_rejects_unexpected_evaluation_fields():
    content = _valid_trajectory_content().replace(
        "- Unexplained differences: none",
        "- Root cause: tool timeout\n- Unexplained differences: none",
    )

    issues = _trajectory_operation_validation_issues(
        "task",
        _valid_trajectory_fields(content=content),
    )

    assert any(
        issue.reason == "trajectory evaluation contains unexpected fields" for issue in issues
    )


def test_trajectory_validation_accepts_explicit_none_and_unknown_step_values():
    content = _valid_trajectory_content().replace(
        "- Decision: report completion\n- Decision basis: assumed the earlier operation succeeded",
        "- Decision: none\n- Decision basis: unknown",
    )

    assert (
        _trajectory_operation_validation_issues(
            "task",
            _valid_trajectory_fields(content=content),
        )
        == []
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
                        memory_fields=_valid_trajectory_fields(
                            outcome="success",
                            retrieval_anchor=(
                                "Stage: final_response; Boundary: completion verification; "
                                "Capability: verify; Target: requested operation; Outcome: success"
                            ),
                            content=(
                                _valid_trajectory_content()
                                .replace("- Outcome: failure", "- Outcome: success")
                                .replace(
                                    "Required outcome: failed; completion was not verified",
                                    "Required outcome: passed; completion was verified",
                                )
                                .replace(
                                    "Failed requirements: completion verification and grounded final response",
                                    "Failed requirements: none",
                                )
                            ),
                        ),
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

    async def stat(self, uri, ctx=None, skip_count=False):
        if uri not in self.files:
            raise FileNotFoundError(uri)
        return {"uri": uri}

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
                    criterion_name="completion_quality",
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
        )
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
    assert '"evidence_sources"' not in fs.writes[0][1]
    assert '"advisory_signals"' not in fs.writes[0][1]
    assert len(analysis.trajectories) == 1
    traj = analysis.trajectories[0]
    assert traj.name == "task"
    assert traj.outcome == "success"
    assert traj.retrieval_anchor.startswith("Stage: final_response")
    assert traj.metadata["case_name"] == "case"
    assert analysis.evaluation.passed is True
    assert analysis.metadata["policy_snapshot_id"] == "snapshot"


@pytest.mark.asyncio
async def test_trajectory_rollout_analyzer_evaluates_before_extracting_trajectory(monkeypatch):
    from openviking.session.train.components import trajectory_analyzer as module

    FakeExtractLoop.created.clear()
    fs = FakeVikingFS()
    evaluator = FakeRolloutEvaluator()
    evaluator_context = {"source": "external_assessor"}
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
    assert len(provider.messages) == 1
    assert provider.messages[0] is rollout.messages[0]
    conversation_message = provider._build_conversation_message()
    assert "## Evidence Sources" in conversation_message["content"]
    assert '"source": "rollout_evaluation"' in conversation_message["content"]
    assert '"direct": true' in conversation_message["content"]
    assert "## Advisory Signals" in conversation_message["content"]
    assert '"available": false' in conversation_message["content"]
    assert '"score": 0.25' in conversation_message["content"]
    assert "reward was zero" in conversation_message["content"]
    assert "missing confirmation" in conversation_message["content"]
    assert analysis.metadata["extraction_message_count"] == 1
    assert analysis.metadata["evidence_source_summary"] == {
        "direct_available": True,
        "source_count": 1,
        "advisory_signal_count": 0,
    }


@pytest.mark.asyncio
async def test_trajectory_rollout_analyzer_can_disable_evaluation_evidence(monkeypatch):
    from openviking.session.train.components import trajectory_analyzer as module

    FakeExtractLoop.created.clear()
    fs = FakeVikingFS()
    evaluator = FakeRolloutEvaluator()
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
        inject_evaluation_feedback=False,
    )

    analysis = await analyzer.analyze(_rollout(), context)

    provider = FakeExtractLoop.created[0].kwargs["context_provider"]
    assert provider._evidence_sources == {
        "direct_available": False,
        "items": [],
        "contract": (
            "Only items with direct=true may prove a material claim. "
            "Other items may only identify what to inspect."
        ),
    }
    assert analysis.metadata["evidence_source_summary"] == {
        "direct_available": False,
        "source_count": 0,
        "advisory_signal_count": 0,
    }


@pytest.mark.asyncio
async def test_trajectory_rollout_analyzer_records_injected_experience_feedback(monkeypatch):
    from openviking.session.train.components import trajectory_analyzer as module

    class FeedbackExtractLoop(FakeExtractLoop):
        async def run(self):
            return (
                ResolvedOperations(
                    upsert_operations=[
                        ResolvedOperation(
                            old_memory_file_content=None,
                            memory_fields=_valid_trajectory_fields(
                                experience_effects=(
                                    '{"positive_ids":[],"negative_ids":["E1"],"weak_ids":[]}'
                                )
                            ),
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

    FakeExtractLoop.created.clear()
    fs = FakeVikingFS()
    exp_uri = "viking://user/u/memories/experiences/payment_guard.md"
    fs.files[exp_uri] = (
        "experience\n\n"
        "<!-- MEMORY_FIELDS\n"
        '{"memory_type":"experiences","experience_name":"payment_guard",'
        '"trigger_code":"def should_trigger(ctx):\\n    return True\\n"}\n'
        "-->"
    )
    monkeypatch.setattr(module, "ExtractLoop", FeedbackExtractLoop)
    monkeypatch.setattr(module, "get_viking_fs", lambda: fs)

    analyzer = TrajectoryRolloutAnalyzer(viking_fs=fs, vlm=SimpleNamespace(model="fake"))
    context = TrajectoryAnalyzerContext(
        request_context=SimpleNamespace(
            user=SimpleNamespace(account_id="default", user_id="u"),
            account_id="default",
        )
    )
    rollout = _rollout()
    rollout.messages[0].parts[0].text = f"""<experience_reminder>
<experience_name>payment_guard</experience_name>
<experience_uri>{exp_uri}</experience_uri>
<triggered_before_tool>book_reservation</triggered_before_tool>
</experience_reminder>"""

    analysis = await analyzer.analyze(rollout, context)

    assert analysis.metadata["experience_feedback_stats"]["updated_uris"] == [exp_uri]
    written = fs.files[exp_uri]
    assert '"feedback_stats"' in written
    assert '"negative_count": 1' in written
