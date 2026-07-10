# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import asyncio


def test_smoke_case_loader_filters_split_domain_and_indices():
    from benchmark.smoke.train.case_loader import SmokeCaseLoader

    loader = SmokeCaseLoader(domain="tickets", split="train", task_indices=[1])

    cases = loader.load_cases()

    assert len(cases) == 1
    assert cases[0].input["dataset"] == "smoke"
    assert cases[0].input["task_id"] == "refund_wrong_amount"
    assert cases[0].task_signature == "smoke:tickets:train:refund_wrong_amount"


def test_smoke_service_wires_generic_dataset_service(monkeypatch):
    import benchmark.smoke.train.service_app as service_app

    captured = {}

    def fake_create_dataset_service_app(**kwargs):
        captured.update(kwargs)
        return kwargs

    monkeypatch.setattr(service_app, "create_dataset_service_app", fake_create_dataset_service_app)

    app = service_app.create_app(max_rollout_concurrency=3, rollout_thread_workers=2)
    loader = app["make_case_loader"]("smoke", "tickets", "test", {"task_indices": [0]})
    executor = app["make_rollout_executor"]({"concurrency": 5, "show_progress": True})

    assert captured["service_name"] == "smoke"
    assert captured["max_rollout_concurrency"] == 3
    assert captured["rollout_thread_workers"] == 2
    assert loader.task_indices == [0]
    assert executor.concurrency == 5
    assert executor.show_progress is True


def test_smoke_rollout_executor_returns_scripted_failure_with_tool_parts():
    from benchmark.smoke.train.case_loader import SmokeCaseLoader
    from benchmark.smoke.train.rollout_executor import SmokeRolloutExecutor
    from openviking.session.train import ExecutionContext, ExperienceSet

    case = SmokeCaseLoader(domain="tickets", split="train", task_indices=[1]).load_cases()[0]
    executor = SmokeRolloutExecutor()

    rollouts = asyncio.run(
        executor.execute(
            [case],
            ExperienceSet(root_uri="viking://user/memories/experiences", policies=[]),
            ExecutionContext(policy_snapshot_id="snapshot-1"),
        )
    )

    rollout = rollouts[0]
    assert rollout.evaluation is not None
    assert rollout.evaluation.passed is False
    assert rollout.evaluation.score == 0.0
    assert any(message.get_tool_parts() for message in rollout.messages)
    assert rollout.metadata["rollout_backend"] == "smoke_scripted"


def test_smoke_rollout_executor_policy_marker_forces_success():
    from benchmark.smoke.train.case_loader import SmokeCaseLoader
    from benchmark.smoke.train.rollout_executor import SmokeRolloutExecutor
    from openviking.session.train import ExecutionContext, Experience, ExperienceSet

    case = SmokeCaseLoader(domain="tickets", split="test", task_indices=[1]).load_cases()[0]
    executor = SmokeRolloutExecutor()
    policy_set = ExperienceSet(
        root_uri="viking://user/memories/experiences",
        policies=[
            Experience(
                name="smoke marker",
                uri="viking://user/memories/experiences/smoke.md",
                version=1,
                status="production",
                content="smoke_pass:eval_missing_notice",
            )
        ],
    )

    rollouts = asyncio.run(
        executor.execute([case], policy_set, ExecutionContext(policy_snapshot_id="snapshot-1"))
    )

    assert rollouts[0].evaluation is not None
    assert rollouts[0].evaluation.passed is True
    assert rollouts[0].metadata["forced_success"] is True


def test_smoke_complex_case_requires_experience_marker():
    from benchmark.smoke.train.case_loader import SmokeCaseLoader
    from benchmark.smoke.train.rollout_executor import SmokeRolloutExecutor
    from openviking.session.train import ExecutionContext, ExperienceSet

    case = SmokeCaseLoader(domain="tickets", split="test", task_indices=[2]).load_cases()[0]

    baseline = asyncio.run(
        SmokeRolloutExecutor().execute(
            [case],
            ExperienceSet(root_uri="viking://user/memories/experiences", policies=[]),
            ExecutionContext(policy_snapshot_id="snapshot-1"),
        )
    )[0]
    assert baseline.evaluation is not None
    assert baseline.evaluation.passed is False
    assert baseline.metadata["forced_success_source"] == "none"

    with_experience = asyncio.run(
        SmokeRolloutExecutor(
            direct_experience_content="经验：复杂联程退款先换券再退差额。"
        ).execute(
            [case],
            ExperienceSet(root_uri="viking://user/memories/experiences", policies=[]),
            ExecutionContext(policy_snapshot_id="snapshot-1"),
        )
    )[0]
    assert with_experience.evaluation is not None
    assert with_experience.evaluation.passed is True
    assert with_experience.metadata["forced_success"] is True
    assert with_experience.metadata["forced_success_source"] == "policy_or_direct_experience"
