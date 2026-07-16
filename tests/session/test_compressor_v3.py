# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.message import Message, TextPart
from openviking.server.identity import RequestContext, Role
from openviking.session import create_session_compressor
from openviking.session.compressor_v3 import SessionCompressorV3, _experience_root_uri
from openviking.session.memory.dataclass import (
    MemoryFile,
    ResolvedOperation,
    ResolvedOperations,
    StoredLink,
)
from openviking.session.memory.memory_updater import MemoryUpdateResult
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.session.train import (
    Case,
    ExperienceSet,
    PolicyApplyResult,
    PolicyPlanItem,
    PolicyUpdatePlan,
    Rollout,
    RolloutAnalysis,
    RolloutTrainingResult,
    Rubric,
    RubricCriterion,
    RubricEvaluation,
    StreamingPolicyTrainerConfig,
    Trajectory,
)
from openviking.session.train.components.session_commit import _case_spec_message_to_request
from openviking_cli.session.user_id import UserIdentifier


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier.the_default_user("u"), role=Role.ROOT)


def _messages() -> list[Message]:
    return [
        Message(
            id="m1",
            role="user",
            parts=[TextPart("请处理重复预订，只取消确认是重复的那一单。")],
        ),
        Message(
            id="m2",
            role="assistant",
            parts=[TextPart("已读取两个预订，确认第二个是重复记录并取消。")],
        ),
    ]


def _case_operation() -> ResolvedOperation:
    return ResolvedOperation(
        old_memory_file_content=None,
        memory_type="cases",
        uris=["viking://user/u/memories/cases/重复预订处理.md"],
        memory_fields={
            "case_name": "重复预订处理",
            "task_signature": "处理重复预订并只取消确认重复的订单",
            "input": '{"summary":"用户要求处理重复预订","preconditions":["存在两个相似预订"]}',
            "rubric": '{"name":"重复预订处理Rubric","description":"成功且高效处理重复预订","criteria":[{"name":"先验证重复","description":"取消前必须确认哪一单是重复订单","required":true,"weight":0.6},{"name":"只取消重复项","description":"不得影响有效订单","required":true,"weight":0.4}]}',
            "evidence": "助手根据读取结果确认重复项并完成取消。",
        },
    )


def test_factory_defaults_to_v3():
    compressor = create_session_compressor(vikingdb=None)
    assert isinstance(compressor, SessionCompressorV3)


def test_factory_ignores_deprecated_memory_version():
    assert isinstance(
        create_session_compressor(vikingdb=None, memory_version="v2"), SessionCompressorV3
    )
    assert isinstance(
        create_session_compressor(vikingdb=None, memory_version="unsupported"),
        SessionCompressorV3,
    )


def test_experience_root_uri_requires_request_user():
    with pytest.raises(ValueError, match="RequestContext.user.user_id is required"):
        _experience_root_uri(SimpleNamespace(user=None))


def test_case_experience_links_require_policy_root_uri():
    traj_uri = "viking://user/u/memories/trajectories/t.md"
    plan = PolicyUpdatePlan(
        items=[
            PolicyPlanItem(
                kind="upsert",
                memory_type="experiences",
                target_name="exp",
                target_uri=None,
                before_content=None,
                after_content="exp",
                links=[
                    StoredLink(
                        from_uri="",
                        to_uri=traj_uri,
                        link_type="derived_from",
                        weight=1.0,
                    )
                ],
            )
        ]
    )
    apply_result = PolicyApplyResult(
        updated_policy_set=ExperienceSet(root_uri="", policies=[]),
        written_uris=["viking://user/u/memories/experiences/exp.md"],
    )

    from openviking.session.compressor_v3 import _case_experience_links_via_trajectories

    with pytest.raises(ValueError, match="updated_policy_set.root_uri is required"):
        _case_experience_links_via_trajectories(
            case_uri="viking://user/u/memories/cases/case.md",
            trajectory_uris={traj_uri},
            plan=plan,
            apply_result=apply_result,
        )


@pytest.mark.asyncio
async def test_train_from_extracted_cases_submits_streaming_rollout(monkeypatch):
    submitted_gradients = []
    submitted_analyses = []

    class FakeTrainer:
        policy_set = ExperienceSet(
            root_uri="viking://user/u/memories/experiences",
            policies=[],
        )

        async def submit_gradients(self, gradients, *, analysis=None, rollout=None):
            submitted_gradients.append(gradients)
            submitted_analyses.append(analysis)
            return RolloutTrainingResult(
                analyses=[analysis] if analysis else [],
                gradients=list(gradients),
                plan=PolicyUpdatePlan(items=[], metadata={}),
                apply_result=PolicyApplyResult(
                    updated_policy_set=self.policy_set,
                    written_uris=[],
                    errors=[],
                ),
                metadata={},
            )

    class FakeAnalyzer:
        async def analyze(self, rollout, context):
            return RolloutAnalysis(
                evaluation=RubricEvaluation(
                    passed=True,
                    score=1.0,
                    criterion_results=[],
                    feedback=[],
                ),
                trajectories=[
                    Trajectory(
                        name="duplicate_booking",
                        uri="viking://user/u/memories/trajectories/t1.md",
                        content="trajectory content",
                        outcome="success",
                        retrieval_anchor="",
                    )
                ],
                gradients=[],
            )

    async def fake_estimate_exp_gradients(self, *args, **kwargs):
        # Return one dummy gradient so we can verify submission
        from openviking.session.memory.dataclass import MemoryFile
        from openviking.session.train import PatchSemanticGradient

        return [
            PatchSemanticGradient(
                before_file=None,
                after_file=MemoryFile(
                    uri="viking://user/u/memories/experiences/e1.md",
                    content="new exp",
                    memory_type="experiences",
                    extra_fields={"experience_name": "e1"},
                ),
                base_version=1,
                rationale="test",
                links=[],
                confidence=0.9,
                metadata={},
            )
        ]

    monkeypatch.setattr(
        "openviking.session.compressor_v3.get_viking_fs",
        lambda: SimpleNamespace(ls=AsyncMock(return_value=[])),
    )
    monkeypatch.setattr(
        "openviking.session.compressor_v3.get_streaming_policy_trainer",
        AsyncMock(return_value=FakeTrainer()),
    )
    monkeypatch.setattr(
        "openviking.session.train.components.gradient_estimator.ExperienceGradientEstimator.estimate",
        fake_estimate_exp_gradients,
    )

    compressor = SessionCompressorV3(
        vikingdb=None,
        rollout_analyzer=FakeAnalyzer(),
        streaming_trainer_config=StreamingPolicyTrainerConfig(
            max_wait_seconds=60,
            max_gradients_per_update=8,
        ),
    )
    cases = [_training_case()]
    result = await compressor.train_from_extracted_cases(
        cases=cases,
        messages=_messages(),
        ctx=_ctx(),
        session_id="s1",
    )

    assert result["case_count"] == 1
    assert result["submitted"] == 1
    assert len(submitted_gradients) == 1
    assert len(submitted_gradients[0]) == 1  # one exp gradient per case
    # Verify analysis was used
    assert submitted_analyses[0] is not None
    assert submitted_analyses[0].trajectories[0].name == "duplicate_booking"
    assert cases[0].name == "duplicate_booking"


@pytest.mark.asyncio
async def test_train_from_extracted_multiple_case_memories_analyzes_bound_rollouts(monkeypatch):
    seen_rollouts = []
    rollout_messages = _messages()

    class FakeTrainer:
        policy_set = ExperienceSet(
            root_uri="viking://user/u/memories/experiences",
            policies=[],
        )

    class FakeAnalyzer:
        async def analyze(self, rollout, context):
            del context
            seen_rollouts.append(rollout)
            return RolloutAnalysis(
                evaluation=RubricEvaluation(
                    passed=True,
                    score=1.0,
                    criterion_results=[],
                    feedback=[],
                ),
                trajectories=[],
                gradients=[],
            )

    async def fake_estimate_exp_gradients(self, *args, **kwargs):
        return []

    monkeypatch.setattr(
        "openviking.session.compressor_v3.get_viking_fs",
        lambda: SimpleNamespace(ls=AsyncMock(return_value=[])),
    )
    monkeypatch.setattr(
        "openviking.session.compressor_v3.get_streaming_policy_trainer",
        AsyncMock(return_value=FakeTrainer()),
    )
    monkeypatch.setattr(
        "openviking.session.train.components.gradient_estimator.ExperienceGradientEstimator.estimate",
        fake_estimate_exp_gradients,
    )

    case_a = _training_case()
    case_b = Case(
        name="case_b",
        task_signature="Handle a second extracted case.",
        input={"summary": "second case"},
        rubric=case_a.rubric,
    )

    compressor = SessionCompressorV3(
        vikingdb=None,
        rollout_analyzer=FakeAnalyzer(),
        streaming_trainer_config=StreamingPolicyTrainerConfig(
            max_wait_seconds=60,
            max_gradients_per_update=8,
        ),
    )

    result = await compressor.train_from_extracted_cases(
        cases=[case_a, case_b],
        messages=rollout_messages,
        ctx=_ctx(),
        session_id="s1",
    )

    assert result["case_count"] == 2
    assert result["submitted"] == 2
    assert [rollout.case.name for rollout in seen_rollouts] == [
        "duplicate_booking",
        "case_b",
    ]
    assert [rollout.messages for rollout in seen_rollouts] == [
        rollout_messages,
        rollout_messages,
    ]


@pytest.mark.asyncio
async def test_v3_extract_uses_patch_merge_without_directory_lock(monkeypatch):
    applied_operations = []
    trained_cases = []

    class DummyRegistry:
        async def initialize_memory_files(self, ctx):
            return None

    class DummyOrchestrator:
        async def run(self):
            return (
                ResolvedOperations(
                    upsert_operations=[_case_operation()],
                    delete_file_contents=[],
                    errors=[],
                ),
                [],
            )

    class FakeStreamingUpdater:
        async def submit(self, request):
            applied_operations.append(request.operations)
            result = MemoryUpdateResult()
            result.add_written(_case_operation().uris[0])
            return SimpleNamespace(operations=request.operations, apply_result=result)

    compressor = SessionCompressorV3(vikingdb=None)
    compressor._get_or_create_react = lambda **kwargs: DummyOrchestrator()

    async def fake_train_from_extracted_cases(**kwargs):
        trained_cases.extend(kwargs["cases"])
        return {"case_count": len(kwargs["cases"]), "submitted": len(kwargs["cases"])}

    compressor.train_from_extracted_cases = fake_train_from_extracted_cases

    monkeypatch.setattr(
        "openviking.session.compressor_v3.get_viking_fs",
        lambda: SimpleNamespace(write_file=AsyncMock()),
    )
    monkeypatch.setattr(
        "openviking.session.compressor_v3.create_default_registry",
        lambda: DummyRegistry(),
    )
    monkeypatch.setattr(
        "openviking.session.compressor_v3.get_streaming_memory_updater",
        AsyncMock(return_value=FakeStreamingUpdater()),
    )

    contexts = await compressor.extract_long_term_memories(
        messages=_messages(),
        ctx=_ctx(),
        allowed_memory_types={"cases", "profile"},
    )

    assert len(applied_operations) == 1
    assert applied_operations[0].upsert_operations[0].memory_type == "cases"
    assert [case.name for case in trained_cases] == ["重复预订处理"]
    assert contexts[0].uri.endswith("重复预订处理.md")


@pytest.mark.asyncio
async def test_v3_extract_trains_only_canonical_case_after_patch_merge(monkeypatch):
    trained_kwargs = []
    canonical_uri = "viking://user/u/memories/cases/duplicate_booking.md"
    loser_uri = "viking://user/u/memories/cases/duplicate_booking_duplicate.md"
    canonical_fields = {
        "case_name": "duplicate_booking",
        "task_signature": "Handle duplicate bookings safely.",
        "input": '{"summary":"cancel only the confirmed duplicate booking"}',
        "rubric": (
            '{"name":"duplicate_booking_rubric","description":"Verify duplicate handling",'
            '"criteria":[{"name":"verify_duplicate","description":"The assistant verifies '
            'which booking is duplicate before cancellation.","required":true,"weight":1.0}]}'
        ),
        "evidence": "Canonical merged case evidence.",
    }

    def case_op(uri: str, name: str) -> ResolvedOperation:
        fields = dict(canonical_fields)
        fields["case_name"] = name
        return ResolvedOperation(
            old_memory_file_content=None,
            memory_type="cases",
            uris=[uri],
            memory_fields=fields,
        )

    class DummyRegistry:
        async def initialize_memory_files(self, ctx):
            return None

    class DummyOrchestrator:
        async def run(self):
            return (
                ResolvedOperations(
                    upsert_operations=[
                        case_op(canonical_uri, "duplicate_booking"),
                        case_op(loser_uri, "duplicate_booking_duplicate"),
                    ],
                    delete_file_contents=[],
                    errors=[],
                ),
                [],
            )

    class FakeFS:
        async def read_file(self, uri, ctx=None):
            del ctx
            if uri != canonical_uri:
                raise FileNotFoundError(uri)
            return MemoryFileUtils.write(
                MemoryFile(
                    uri=canonical_uri,
                    content="",
                    memory_type="cases",
                    extra_fields=dict(canonical_fields),
                )
            )

        async def write_file(self, uri, content, ctx=None):
            del uri, content, ctx

    class FakeStreamingUpdater:
        async def submit(self, request):
            del request
            result = MemoryUpdateResult()
            result.add_written(canonical_uri)
            return SimpleNamespace(
                operations=ResolvedOperations(
                    upsert_operations=[case_op(canonical_uri, "duplicate_booking")],
                    delete_file_contents=[],
                    errors=[],
                ),
                apply_result=result,
            )

    compressor = SessionCompressorV3(vikingdb=None)
    compressor._get_or_create_react = lambda **kwargs: DummyOrchestrator()

    async def fake_train_from_extracted_cases(**kwargs):
        trained_kwargs.append(kwargs)
        return {"case_count": len(kwargs["cases"]), "submitted": len(kwargs["cases"])}

    compressor.train_from_extracted_cases = fake_train_from_extracted_cases

    monkeypatch.setattr("openviking.session.compressor_v3.get_viking_fs", lambda: FakeFS())
    monkeypatch.setattr(
        "openviking.session.compressor_v3.create_default_registry",
        lambda: DummyRegistry(),
    )
    monkeypatch.setattr(
        "openviking.session.compressor_v3.get_streaming_memory_updater",
        AsyncMock(return_value=FakeStreamingUpdater()),
    )

    contexts = await compressor.extract_long_term_memories(
        messages=_messages(),
        ctx=_ctx(),
        allowed_memory_types={"cases", "profile"},
    )

    assert [context.uri for context in contexts] == [canonical_uri]
    assert len(trained_kwargs) == 1
    assert [case.name for case in trained_kwargs[0]["cases"]] == ["duplicate_booking"]
    assert trained_kwargs[0]["cases"][0].metadata["case_uris"] == [canonical_uri]
    assert trained_kwargs[0]["case_uri_by_name"] == {"duplicate_booking": canonical_uri}


def _training_case() -> Case:
    return Case(
        name="duplicate_booking",
        task_signature="Handle duplicate bookings safely.",
        input={"summary": "cancel only the duplicate booking", "task_id": "task-1"},
        rubric=Rubric(
            name="duplicate_booking_rubric",
            description="Verify duplicates before cancellation.",
            criteria=[
                RubricCriterion(
                    name="verify_duplicate",
                    description="The assistant verifies which booking is the duplicate before acting.",
                    required=True,
                    weight=1.0,
                )
            ],
        ),
        metadata={"evidence": "The rollout contains duplicate-booking handling evidence."},
    )


def _case_spec_message(case: Case | None = None) -> Message:
    rollout = Rollout(
        case=case or _training_case(),
        messages=[],
        policy_snapshot_id="snapshot-1",
    )
    request = _case_spec_message_to_request(rollout)
    return Message(
        id="case-spec",
        role="system",
        parts=[TextPart(text=request["parts"][0]["text"])],
    )


@pytest.mark.asyncio
async def test_v3_training_case_spec_fast_path_skips_user_memory_extraction_and_strips_control_message():
    case_spec = _case_spec_message()
    rollout_messages = _messages()
    written = []
    trained = []

    compressor = SessionCompressorV3(vikingdb=None, rollout_analyzer=SimpleNamespace())

    async def fail_extract_user_memories(**kwargs):
        raise AssertionError("fast path must not run LLM user-memory extraction")

    async def fake_write_training_case_memory(**kwargs):
        written.append(kwargs["case"])
        result = MemoryUpdateResult()
        result.add_written("viking://user/u/memories/cases/duplicate_booking.md")
        return result

    async def fake_train_from_extracted_cases(**kwargs):
        trained.append(kwargs)
        return {"case_count": len(kwargs["cases"]), "submitted": len(kwargs["cases"])}

    compressor._extract_user_memories = fail_extract_user_memories
    compressor._write_training_case_memory = fake_write_training_case_memory
    compressor.train_from_extracted_cases = fake_train_from_extracted_cases

    contexts = await compressor.extract_long_term_memories(
        messages=[case_spec, *rollout_messages],
        ctx=_ctx(),
        session_id="s1",
        archive_uri="viking://user/u/sessions/s1/history/archive_001",
        allowed_memory_types={"cases", "trajectories", "experiences"},
    )

    assert [case.name for case in written] == ["duplicate_booking"]
    assert [case.name for case in trained[0]["cases"]] == ["duplicate_booking"]
    assert trained[0]["messages"] == rollout_messages
    assert contexts[0].uri == "viking://user/u/memories/cases/duplicate_booking.md"


@pytest.mark.asyncio
async def test_v3_training_case_spec_fast_path_not_used_with_user_memory_policy():
    extracted = False
    trained = []

    compressor = SessionCompressorV3(vikingdb=None, rollout_analyzer=SimpleNamespace())

    async def fake_extract_user_memories(**kwargs):
        nonlocal extracted
        extracted = True
        return SimpleNamespace(contexts=[], cases=[])

    async def fake_train_from_extracted_cases(**kwargs):
        trained.append(kwargs)
        return {"case_count": 0, "submitted": 0}

    compressor._extract_user_memories = fake_extract_user_memories
    compressor.train_from_extracted_cases = fake_train_from_extracted_cases

    contexts = await compressor.extract_long_term_memories(
        messages=[_case_spec_message(), *_messages()],
        ctx=_ctx(),
        allowed_memory_types={"cases", "profile"},
    )

    assert contexts == []
    assert extracted is True
    assert trained and trained[0]["messages"][0].id == "case-spec"


@pytest.mark.asyncio
async def test_v3_training_case_spec_fast_path_rejects_invalid_protocol():
    message = _case_spec_message()
    assert isinstance(message.parts[0], TextPart)
    message.parts[0].text = message.parts[0].text.replace(
        "openviking.batch_train.case_spec.v1",
        "openviking.batch_train.case_spec.v0",
    )
    compressor = SessionCompressorV3(vikingdb=None, rollout_analyzer=SimpleNamespace())

    with pytest.raises(ValueError, match="protocol mismatch"):
        await compressor.extract_long_term_memories(
            messages=[message, *_messages()],
            ctx=_ctx(),
            allowed_memory_types={"cases", "trajectories", "experiences"},
        )


def test_training_case_spec_message_uses_fast_path_protocol():
    message = _case_spec_message()
    part = message.parts[0]
    assert isinstance(part, TextPart)
    text = part.text

    assert text.startswith("# OpenViking Batch Training CaseSpec v1")
    assert "openviking.batch_train.case_spec.v1" in text
    assert "duplicate_booking_rubric" in text


def test_training_case_spec_message_uses_original_case_name_for_trials():
    case = _training_case()
    case.name = "tau2_airline_train_1_t0"
    case.task_signature = "tau2:airline:train:1:trial:0"
    case.input.update(
        {
            "domain": "airline",
            "split": "train",
            "data_split": "airline_train",
            "task_id": "1",
            "task_no": 1,
            "train_trial": 0,
            "original_case_name": "tau2_airline_train_1",
        }
    )
    message = _case_spec_message(case)
    payload = __import__(
        "openviking.session.compressor_v3", fromlist=["_training_case_spec_payload_from_message"]
    )._training_case_spec_payload_from_message(message)

    assert payload["case"]["name"] == "tau2_airline_train_1"
    assert payload["case"]["task_signature"] == "tau2:airline:train:1"
    assert payload["case"]["metadata"]["rollout_case_name"] == "tau2_airline_train_1_t0"


@pytest.mark.asyncio
async def test_v3_fast_path_writes_final_memory_diff_with_case_traj_and_exp(monkeypatch):
    archive_uri = "viking://user/u/sessions/s1/history/archive_001"
    writes: dict[str, str] = {}

    class FakeFS:
        async def write_file(self, uri, content, ctx=None):
            del ctx
            writes[uri] = content

        async def read_file(self, uri, ctx=None):
            del ctx
            if uri.endswith("/cases/duplicate_booking.md"):
                return "# duplicate_booking\n\n<!-- MEMORY_FIELDS\n{}\n-->"
            if uri.endswith("/experiences/booking_duplicate_handling.md"):
                return "new exp content\n\n<!-- MEMORY_FIELDS\n{}\n-->"
            raise FileNotFoundError(uri)

    compressor = SessionCompressorV3(vikingdb=None, rollout_analyzer=SimpleNamespace())

    async def fake_write_training_case_memory(**kwargs):
        result = MemoryUpdateResult()
        result.add_written("viking://user/u/memories/cases/duplicate_booking.md")
        return SimpleNamespace(
            result=result,
            memory_diff={
                "archive_uri": archive_uri,
                "trace_id": None,
                "extracted_at": "now",
                "operations": {
                    "adds": [
                        {
                            "uri": "viking://user/u/memories/cases/duplicate_booking.md",
                            "memory_type": "cases",
                            "after": "# duplicate_booking",
                        }
                    ],
                    "updates": [],
                    "deletes": [],
                },
                "summary": {"total_adds": 1, "total_updates": 0, "total_deletes": 0},
            },
        )

    async def fake_train_from_extracted_cases(**kwargs):
        return {
            "case_count": 1,
            "submitted": 1,
            "memory_diff": {
                "archive_uri": archive_uri,
                "trace_id": None,
                "extracted_at": "now",
                "operations": {
                    "adds": [
                        {
                            "uri": "viking://user/u/memories/trajectories/duplicate_booking.md",
                            "memory_type": "trajectories",
                            "after": "trajectory content",
                        }
                    ],
                    "updates": [
                        {
                            "uri": "viking://user/u/memories/experiences/booking_duplicate_handling.md",
                            "memory_type": "experiences",
                            "before": "old exp content",
                            "after": "new exp content",
                        }
                    ],
                    "deletes": [],
                },
                "summary": {"total_adds": 1, "total_updates": 1, "total_deletes": 0},
            },
        }

    compressor._write_training_case_memory = fake_write_training_case_memory
    compressor.train_from_extracted_cases = fake_train_from_extracted_cases
    monkeypatch.setattr("openviking.session.compressor_v3.get_viking_fs", lambda: FakeFS())

    contexts = await compressor.extract_long_term_memories(
        messages=[_case_spec_message(), *_messages()],
        ctx=_ctx(),
        session_id="s1",
        archive_uri=archive_uri,
        allowed_memory_types={"cases", "trajectories", "experiences"},
    )

    assert contexts[0].uri.endswith("/cases/duplicate_booking.md")
    diff = __import__("json").loads(writes[f"{archive_uri}/memory_diff.json"])
    assert [item["memory_type"] for item in diff["operations"]["adds"]] == [
        "cases",
        "trajectories",
    ]
    assert [item["memory_type"] for item in diff["operations"]["updates"]] == ["experiences"]
    assert diff["summary"] == {"total_adds": 2, "total_updates": 1, "total_deletes": 0}


@pytest.mark.asyncio
async def test_v3_builds_training_memory_diff_from_streaming_result(monkeypatch):
    archive_uri = "viking://user/u/sessions/s1/history/archive_001"

    class FakeFS:
        async def read_file(self, uri, ctx=None):
            del ctx
            assert uri.endswith("/experiences/booking_duplicate_handling.md")
            return "new exp content\n\n<!-- MEMORY_FIELDS\n{}\n-->"

    compressor = SessionCompressorV3(vikingdb=None, rollout_analyzer=SimpleNamespace())
    plan = PolicyUpdatePlan(
        items=[
            PolicyPlanItem(
                kind="upsert",
                memory_type="experiences",
                target_name="booking_duplicate_handling",
                target_uri="viking://user/u/memories/experiences/booking_duplicate_handling.md",
                before_content="old exp content",
                after_content="new exp content fallback",
                links=[
                    StoredLink(
                        from_uri="viking://user/u/memories/experiences/booking_duplicate_handling.md",
                        to_uri="viking://user/u/memories/trajectories/duplicate_booking.md",
                        link_type="derived_from",
                        weight=1.0,
                    )
                ],
            )
        ]
    )
    training_result = RolloutTrainingResult(
        analyses=[
            RolloutAnalysis(
                evaluation=RubricEvaluation(
                    passed=True,
                    score=1.0,
                    criterion_results=[],
                    feedback=[],
                ),
                trajectories=[
                    Trajectory(
                        name="duplicate_booking",
                        uri="viking://user/u/memories/trajectories/duplicate_booking.md",
                        content="trajectory content",
                        outcome="success",
                        retrieval_anchor="Stage: final",
                    )
                ],
            )
        ],
        gradients=[],
        plan=plan,
        apply_result=PolicyApplyResult(
            updated_policy_set=ExperienceSet(
                root_uri="viking://user/u/memories/experiences",
                policies=[],
            ),
            written_uris=["viking://user/u/memories/experiences/booking_duplicate_handling.md"],
        ),
    )

    diff = await compressor._build_training_memory_diff(
        training_result=training_result,
        viking_fs=FakeFS(),
        ctx=_ctx(),
        archive_uri=archive_uri,
    )

    assert diff["summary"] == {"total_adds": 1, "total_updates": 1, "total_deletes": 0}
    assert diff["operations"]["adds"][0]["memory_type"] == "trajectories"
    update = diff["operations"]["updates"][0]
    assert update["memory_type"] == "experiences"
    assert update["before"] == "old exp content"
    assert update["after"] == "new exp content"


@pytest.mark.asyncio
async def test_v3_training_memory_diff_filters_batch_items_by_current_analysis_trajectory(
    monkeypatch,
):
    archive_uri = "viking://user/u/sessions/s1/history/archive_001"
    traj_a = "viking://user/u/memories/trajectories/traj_a.md"
    traj_b = "viking://user/u/memories/trajectories/traj_b.md"
    exp_a = "viking://user/u/memories/experiences/exp_a.md"
    exp_b = "viking://user/u/memories/experiences/exp_b.md"

    class FakeFS:
        async def read_file(self, uri, ctx=None):
            del ctx
            return {
                exp_a: "exp a\n\n<!-- MEMORY_FIELDS\n{}\n-->",
                exp_b: "exp b\n\n<!-- MEMORY_FIELDS\n{}\n-->",
            }[uri]

    compressor = SessionCompressorV3(vikingdb=None, rollout_analyzer=SimpleNamespace())
    training_result = RolloutTrainingResult(
        analyses=[
            RolloutAnalysis(
                evaluation=RubricEvaluation(
                    passed=True, score=1.0, criterion_results=[], feedback=[]
                ),
                trajectories=[
                    Trajectory(
                        name="traj_a",
                        uri=traj_a,
                        content="trajectory a",
                        outcome="success",
                        retrieval_anchor="",
                    )
                ],
            )
        ],
        gradients=[],
        plan=PolicyUpdatePlan(
            items=[
                PolicyPlanItem(
                    kind="upsert",
                    memory_type="experiences",
                    target_name="exp_a",
                    target_uri=exp_a,
                    before_content=None,
                    after_content="exp a fallback",
                    links=[
                        StoredLink(
                            from_uri=exp_a,
                            to_uri=traj_a,
                            link_type="derived_from",
                            weight=1.0,
                        )
                    ],
                ),
                PolicyPlanItem(
                    kind="upsert",
                    memory_type="experiences",
                    target_name="exp_b",
                    target_uri=exp_b,
                    before_content=None,
                    after_content="exp b fallback",
                    links=[
                        StoredLink(
                            from_uri=exp_b,
                            to_uri=traj_b,
                            link_type="derived_from",
                            weight=1.0,
                        )
                    ],
                ),
            ]
        ),
        apply_result=PolicyApplyResult(
            updated_policy_set=ExperienceSet(
                root_uri="viking://user/u/memories/experiences",
                policies=[],
            ),
            written_uris=[exp_a, exp_b],
        ),
    )

    diff = await compressor._build_training_memory_diff(
        training_result=training_result,
        viking_fs=FakeFS(),
        ctx=_ctx(),
        archive_uri=archive_uri,
    )

    assert diff["summary"] == {"total_adds": 2, "total_updates": 0, "total_deletes": 0}
    assert [op["uri"] for op in diff["operations"]["adds"]] == [traj_a, exp_a]


@pytest.mark.asyncio
async def test_v3_training_links_case_to_trajectory_and_experience_via_trajectory(monkeypatch):
    case_uri = "viking://user/u/memories/cases/duplicate_booking.md"
    traj_uri = "viking://user/u/memories/trajectories/duplicate_booking.md"
    exp_uri = "viking://user/u/memories/experiences/booking_duplicate_handling.md"
    deleted_exp_uri = "viking://user/u/memories/experiences/legacy_booking_handling.md"

    class FakeFS:
        def __init__(self):
            self.commits = []
            self.files = {
                case_uri: MemoryFileUtils.write(
                    MemoryFile(
                        uri=case_uri,
                        content="# duplicate_booking",
                        memory_type="cases",
                        extra_fields={"memory_type": "cases", "case_name": "duplicate_booking"},
                    ),
                    content_template=(
                        "# {{ case_name }}\n\n"
                        "## Linked Experiences\n"
                        "{% for link in links or [] %}"
                        "{% set target_uri = link.to_uri or '' %}"
                        "{% if '/memories/experiences/' in target_uri %}"
                        "- [{{ uri_basename(target_uri) }}]({{ link_target(target_uri) }})\n"
                        "{% endif %}"
                        "{% endfor %}"
                    ),
                ),
                traj_uri: MemoryFileUtils.write(
                    MemoryFile(
                        uri=traj_uri,
                        content="trajectory content",
                        memory_type="trajectories",
                        extra_fields={
                            "memory_type": "trajectories",
                            "trajectory_name": "duplicate_booking",
                        },
                        backlinks=[
                            StoredLink(
                                from_uri=exp_uri,
                                to_uri=traj_uri,
                                link_type="derived_from",
                                weight=1.0,
                                match_text=None,
                                description="",
                            ).model_dump()
                        ],
                    )
                ),
                exp_uri: MemoryFileUtils.write(
                    MemoryFile(
                        uri=exp_uri,
                        content="old exp content",
                        memory_type="experiences",
                        extra_fields={
                            "memory_type": "experiences",
                            "experience_name": "booking_duplicate_handling",
                        },
                    )
                ),
            }

        async def read_file(self, uri, ctx=None):
            del ctx
            return self.files[uri]

        async def write_file(self, uri, content, ctx=None):
            del ctx
            self.files[uri] = content

        async def ls(self, uri, output="original", ctx=None):
            del uri, output, ctx
            return []

        async def commit(self, **kwargs):
            self.commits.append(kwargs)

    class FakeTrainer:
        policy_set = ExperienceSet(root_uri="viking://user/u/memories/experiences", policies=[])

        async def submit_gradients(self, gradients, *, analysis=None, rollout=None):
            del gradients, analysis, rollout
            plan = PolicyUpdatePlan(
                items=[
                    PolicyPlanItem(
                        kind="upsert",
                        memory_type="experiences",
                        target_name="booking_duplicate_handling",
                        target_uri=exp_uri,
                        before_content="old exp content",
                        after_content="new exp content",
                        links=[
                            StoredLink(
                                from_uri=exp_uri,
                                to_uri=traj_uri,
                                link_type="derived_from",
                                weight=1.0,
                                match_text=None,
                                description="",
                            )
                        ],
                    )
                ]
            )
            return RolloutTrainingResult(
                analyses=[],
                gradients=[],
                plan=plan,
                apply_result=PolicyApplyResult(
                    updated_policy_set=ExperienceSet(
                        root_uri="viking://user/u/memories/experiences",
                        policies=[],
                    ),
                    written_uris=[exp_uri],
                    deleted_uris=[deleted_exp_uri],
                    errors=[],
                ),
                metadata={},
            )

    class FakeAnalyzer:
        async def analyze(self, rollout, context):
            del rollout, context
            return RolloutAnalysis(
                evaluation=RubricEvaluation(
                    passed=True, score=1.0, criterion_results=[], feedback=[]
                ),
                trajectories=[
                    Trajectory(
                        name="duplicate_booking",
                        uri=traj_uri,
                        content="trajectory content",
                        outcome="success",
                        retrieval_anchor="",
                    )
                ],
                gradients=[],
            )

    async def fake_estimate_exp_gradients(self, *args, **kwargs):
        from openviking.session.train import PatchSemanticGradient

        return [
            PatchSemanticGradient(
                before_file=None,
                after_file=MemoryFile(
                    uri=exp_uri,
                    content="new exp content",
                    memory_type="experiences",
                    extra_fields={"experience_name": "booking_duplicate_handling"},
                ),
                base_version=1,
                rationale="test",
                links=[],
                confidence=0.9,
                metadata={},
            )
        ]

    fs = FakeFS()
    monkeypatch.setattr("openviking.session.compressor_v3.get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        "openviking.session.compressor_v3.get_streaming_policy_trainer",
        AsyncMock(return_value=FakeTrainer()),
    )
    monkeypatch.setattr(
        "openviking.session.train.components.gradient_estimator.ExperienceGradientEstimator.estimate",
        fake_estimate_exp_gradients,
    )

    compressor = SessionCompressorV3(vikingdb=None, rollout_analyzer=FakeAnalyzer())
    result = await compressor.train_from_extracted_cases(
        cases=[_training_case()],
        case_uri_by_name={"duplicate_booking": case_uri},
        messages=_messages(),
        ctx=_ctx(),
        session_id="session-1",
        archive_uri="viking://user/u/sessions/session-1/history/archive_001",
    )

    assert result["submitted"] == 1
    case_file = MemoryFileUtils.read(fs.files[case_uri], uri=case_uri)
    assert any(
        link["to_uri"] == traj_uri
        and link["link_type"] == "related_to"
        and link.get("match_text") is None
        and link.get("description") == ""
        for link in case_file.links
    )
    assert any(
        link["to_uri"] == exp_uri
        and link["link_type"] == "related_to"
        and link.get("match_text") is None
        and link.get("description") == ""
        for link in case_file.links
    )
    linked_experiences_section = (
        fs.files[case_uri].split("## Linked Experiences", 1)[1].split("<!-- MEMORY_FIELDS", 1)[0]
    )
    assert (
        "[booking_duplicate_handling](../experiences/booking_duplicate_handling.md)"
        in linked_experiences_section
    )
    assert "duplicate_booking.md" not in linked_experiences_section
    traj_file = MemoryFileUtils.read(fs.files[traj_uri], uri=traj_uri)
    assert any(link["from_uri"] == case_uri for link in traj_file.backlinks)
    exp_file = MemoryFileUtils.read(fs.files[exp_uri], uri=exp_uri)
    assert any(link["from_uri"] == case_uri for link in exp_file.backlinks)
    assert fs.commits == [
        {
            "message": "Update experience memories from session commit archive_001",
            "paths": [exp_uri, deleted_exp_uri],
            "ctx": _ctx(),
        }
    ]
