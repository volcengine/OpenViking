# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Core commit outcomes: shared extraction, precise links, and per-case evaluation."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.message import Message, TextPart
from openviking.server.identity import RequestContext, Role
from openviking.session.compressor_v3 import SessionCompressorV3
from openviking.session.memory.dataclass import MemoryFile, ResolvedOperation, ResolvedOperations
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.session.train import (
    Case,
    ExperienceSet,
    PolicyApplyResult,
    PolicyPlanItem,
    PolicyUpdatePlan,
    RolloutTrainingResult,
    Rubric,
    RubricEvaluation,
)
from openviking.session.train.components.trajectory_analyzer import (
    TrajectoryAnalyzerContext,
    TrajectoryRolloutAnalyzer,
)
from openviking_cli.session.user_id import UserIdentifier


class CommitFS:
    agfs = None

    def __init__(self):
        self.files = {}

    async def read_file(self, uri, ctx=None):
        if uri not in self.files:
            raise FileNotFoundError(uri)
        return self.files[uri]

    async def write_file(self, uri, content, ctx=None, **kwargs):
        self.files[uri] = content

    async def ls(self, *args, **kwargs):
        return []


class CommitTrainer:
    def __init__(self, fs):
        self.fs = fs
        self.policy_set = ExperienceSet(
            root_uri="viking://user/u/memories/experiences", policies=[]
        )
        self.calls = []

    async def submit_gradients(self, gradients, *, analysis, rollout, batch_finalizer=None):
        self.calls.append((analysis, gradients))
        items = []
        for gradient in gradients:
            after = MemoryFileUtils.write(gradient.after_file)
            uri = gradient.target_uri
            items.append(
                PolicyPlanItem(
                    kind="upsert",
                    memory_type=gradient.after_file.memory_type,
                    target_name=gradient.target_name,
                    target_uri=uri,
                    before_content=self.fs.files.get(uri),
                    after_content=after,
                    links=gradient.links,
                )
            )
            self.fs.files[uri] = after
        result = RolloutTrainingResult(
            analyses=[analysis],
            gradients=gradients,
            plan=PolicyUpdatePlan(items=items),
            apply_result=PolicyApplyResult(
                updated_policy_set=self.policy_set,
                written_uris=[item.target_uri for item in items],
            ),
        )
        if batch_finalizer:
            await batch_finalizer(result)
        return result


@pytest.fixture
def commit_runtime(monkeypatch):
    config = SimpleNamespace(
        output_language_override="",
        language_fallback="en",
        memory=SimpleNamespace(
            eager_prefetch=False,
            prefetch_search_topn=5,
            link_enabled=False,
            custom_templates_dir=None,
            experimental_memory_switch=False,
            maintenance_review_tokens=4096,
            extraction_output_format="json",
            session_skill_extraction_enabled=False,
        ),
    )
    for module in (
        "openviking_cli.utils.config",
        "openviking.session.memory.utils.language",
        "openviking.session.memory.session_extract_context_provider",
        "openviking.session.memory.extract_loop",
        "openviking.session.compressor_v3",
    ):
        monkeypatch.setattr(f"{module}.get_openviking_config", lambda: config)
    fs = CommitFS()
    trainer, skill_trainer = CommitTrainer(fs), CommitTrainer(fs)
    monkeypatch.setattr("openviking.session.compressor_v3.get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        "openviking.session.compressor_v3.get_streaming_policy_trainer",
        AsyncMock(return_value=trainer),
    )
    monkeypatch.setattr(
        SessionCompressorV3, "_get_session_skill_trainer", AsyncMock(return_value=skill_trainer)
    )

    async def experience_output(self, trajectory, context):
        return ResolvedOperations(
            upsert_operations=[
                ResolvedOperation(
                    memory_type="experiences",
                    uris=[f"{trainer.policy_set.root_uri}/{trajectory.name}.md"],
                    old_memory_file_content=None,
                    memory_fields={"experience_name": trajectory.name, "content": "Learned lesson"},
                )
            ],
            delete_file_contents=[],
            errors=[],
        )

    monkeypatch.setattr(
        "openviking.session.train.components.gradient_estimator.ExperienceGradientEstimator._run_extract_loop",
        experience_output,
    )
    ctx = RequestContext(user=UserIdentifier.the_default_user("u"), role=Role.ROOT)
    cases = [
        Case(
            name=name,
            task_signature=name,
            input={"summary": name},
            rubric=Rubric(name="r", description="task completed", criteria=[]),
        )
        for name in ("book", "cancel")
    ]
    case_uris = {case.name: f"viking://user/u/memories/cases/{case.name}.md" for case in cases}
    for case in cases:
        uri = case_uris[case.name]
        fs.files[uri] = MemoryFileUtils.write(
            MemoryFile(
                uri=uri,
                memory_type="cases",
                content=case.name,
                extra_fields={"case_name": case.name},
            )
        )
    return SimpleNamespace(
        config=config,
        fs=fs,
        trainer=trainer,
        skill_trainer=skill_trainer,
        ctx=ctx,
        cases=cases,
        case_uris=case_uris,
    )


def _model_response(rows, output_format, *, skill=False):
    operations = {"trajectories": []}
    for index, (name, ids) in enumerate(rows):
        fields = {
            "trajectory_name": name,
            "outcome": "success",
            "task_query": "Book and cancel",
            "retrieval_anchor": name,
            "content": f"# {name}\nExecution steps",
            "related_case_ids": ids or [],
        }
        operations["trajectories"].append({"page_id": 100 + index, **fields})
    if skill:
        operations["session_skills"] = [
            {
                "page_id": 200,
                "skill_name": "booking",
                "description": "Booking workflow",
                "content": "## Workflow\nVerify the booking first.",
            }
        ]
    if output_format == "json":
        return json.dumps(operations)
    return "\n".join(
        [
            f"sdk.create_{kind}("
            + ", ".join(f"{key}={value!r}" for key, value in fields.items() if key != "page_id")
            + ")"
            for kind, items in operations.items()
            for fields in items
        ]
        + ["sdk.commit()"]
    )


async def _commit(
    runtime, rows, *, evaluator=None, single=False, output_format="json", skill=False
):
    runtime.config.memory.extraction_output_format = output_format
    runtime.config.memory.session_skill_extraction_enabled = skill
    responses = iter(rows)
    model = SimpleNamespace(
        model="test",
        get_completion_async=AsyncMock(
            side_effect=lambda **kwargs: _model_response(
                next(responses), output_format, skill=skill
            )
        ),
    )
    analyzer = TrajectoryRolloutAnalyzer(viking_fs=runtime.fs, vlm=model, evaluator=evaluator)
    compressor = SessionCompressorV3(
        vikingdb=None,
        rollout_analyzer=analyzer,
        skill_processor=object() if skill else None,
    )
    result = await compressor.train_from_extracted_cases(
        cases=runtime.cases[:1] if single else runtime.cases,
        case_uri_by_name=runtime.case_uris,
        messages=[
            Message(
                id="m",
                role="user",
                parts=[TextPart("Book a trip, then cancel it.")],
                created_at="2026-10-19T00:00:00",
            )
        ],
        ctx=runtime.ctx,
        archive_uri="viking://user/u/sessions/s/history/archive_001",
        collect_memory_diff=True,
        strict_extract_errors=True,
    )
    return result, model.get_completion_async


@pytest.mark.asyncio
@pytest.mark.parametrize("output_format", ["json", "python"])
@pytest.mark.parametrize("single", [True, False])
async def test_commit_extracts_once_and_persists_precise_case_links(
    commit_runtime, output_format, single
):
    runtime = commit_runtime
    rows = (
        [("book", None)]
        if single
        else [
            ("book", ["c1"]),
            ("cancel", ["c2"]),
            ("shared", ["c1", "c2"]),
            ("other", []),
        ]
    )
    result, model = await _commit(
        runtime, [rows], single=single, output_format=output_format, skill=True
    )

    assert result["submitted"] == (1 if single else 2)
    assert model.await_count == len(runtime.trainer.calls) == len(runtime.skill_trainer.calls) == 1
    files = {uri: MemoryFileUtils.read(raw, uri=uri) for uri, raw in runtime.fs.files.items()}
    trajectories = {
        mf.extra_fields["trajectory_name"]: mf
        for uri, mf in files.items()
        if "/memories/trajectories/" in uri
    }
    assert set(trajectories) == {name for name, _ in rows}
    for case in runtime.cases[:1] if single else runtime.cases:
        names = {"book"} if single else {case.name, "shared"}
        targets = {link["to_uri"] for link in files[runtime.case_uris[case.name]].links}
        assert targets == {trajectories[name].uri for name in names} | {
            f"{runtime.trainer.policy_set.root_uri}/{name}.md" for name in names
        }
        assert all(
            any(link["from_uri"] == runtime.case_uris[case.name] for link in files[uri].backlinks)
            for uri in targets
        )
    assert all("related_case_ids" not in mf.extra_fields for mf in trajectories.values())
    adds = result["memory_diff"]["operations"]["adds"]
    assert len({item["uri"] for item in adds if item["memory_type"] == "trajectories"}) == len(rows)
    assert len([item for item in adds if item["memory_type"] == "trajectories"]) == len(rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("evaluation_mode", ["same", "different_feedback", "scores_only"])
async def test_commit_preserves_evaluator_training_semantics(
    commit_runtime, monkeypatch, evaluation_mode
):
    runtime = commit_runtime
    same = evaluation_mode == "same"
    evaluations = [
        RubricEvaluation(passed=True, score=1.0, criterion_results=[], feedback=[]),
        RubricEvaluation(
            passed=same, score=1.0 if same else 0.0, criterion_results=[], feedback=[]
        ),
    ]
    evaluator = SimpleNamespace(evaluate=AsyncMock(side_effect=evaluations))
    if evaluation_mode == "scores_only":
        monkeypatch.setattr(
            "openviking.session.compressor_v3.TrajectoryAnalyzerContext",
            lambda **kwargs: TrajectoryAnalyzerContext(**kwargs, inject_evaluation_feedback=False),
        )
    separate_extraction = evaluation_mode == "different_feedback"
    rows = (
        [[("shared", None)], [("shared", None)]]
        if separate_extraction
        else [
            [
                ("book", ["c1"]),
                ("cancel", ["c2"]),
                ("shared", ["c1", "c2"]),
                ("other", []),
            ]
        ]
    )
    result, model = await _commit(runtime, rows, evaluator=evaluator)

    assert result["submitted"] == evaluator.evaluate.await_count == 2
    assert model.await_count == (2 if separate_extraction else 1)
    calls = runtime.trainer.calls
    assert [analysis.evaluation.score for analysis, _ in calls] == ([1.0] if same else [1.0, 0.0])
    assert [gradients[0].confidence for _, gradients in calls] == pytest.approx(
        [0.9] if same else [0.9, 0.7]
    )
    expected_training = (
        [{"book", "cancel", "shared", "other"}]
        if same
        else (
            [{"shared"}, {"shared"}]
            if separate_extraction
            else [{"book", "shared"}, {"cancel", "shared"}]
        )
    )
    assert [{t.name for t in analysis.trajectories} for analysis, _ in calls] == expected_training
    adds = result["memory_diff"]["operations"]["adds"]
    assert len([item for item in adds if item["memory_type"] == "trajectories"]) == sum(
        map(len, rows)
    )
    case_trajectories = []
    for case in runtime.cases:
        uri = runtime.case_uris[case.name]
        mf = MemoryFileUtils.read(runtime.fs.files[uri], uri=uri)
        expected_names = {"shared"} if separate_extraction else {case.name, "shared"}
        assert {
            link["to_uri"] for link in mf.links if "/memories/experiences/" in link["to_uri"]
        } == {f"{runtime.trainer.policy_set.root_uri}/{name}.md" for name in expected_names}
        trajectory_uris = {
            link["to_uri"] for link in mf.links if "/memories/trajectories/" in link["to_uri"]
        }
        case_trajectories.append(trajectory_uris)
        if separate_extraction:
            assert len(trajectory_uris) == 1
            trajectory_uri = next(iter(trajectory_uris))
            trajectory = MemoryFileUtils.read(runtime.fs.files[trajectory_uri], uri=trajectory_uri)
            assert trajectory.extra_fields["case_uris"] == [uri]
    if separate_extraction:
        assert case_trajectories[0].isdisjoint(case_trajectories[1])
