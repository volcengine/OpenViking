# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest

from openviking.session.train.components import policy_optimizer as policy_optimizer_module
from openviking.session.train.components.policy_optimizer import (
    PatchMergePolicyOptimizer,
    PatchMergePolicyOptimizerContext,
)
from openviking.session.train.components.policy_trainer import _collect_gate_attempts
from openviking.session.train.gates import GateDecision, GateReport, make_gate_audit_attempt


def test_gate_audit_attempt_records_targets_without_derived_or_verbose_fields():
    allowed = SimpleNamespace(target_name="accepted", target_uri="viking://experiences/accepted.md")
    rejected = SimpleNamespace(target_name="rejected", target_uri=None)
    report = GateReport(
        stage="post_gradient",
        evaluated_count=2,
        allowed_count=1,
        rejected_count=1,
        decisions=[
            GateDecision(
                gate_name="quality",
                action="reject",
                reason="unsupported rule",
                evidence={"target_name": "rejected", "raw_response_preview": "verbose"},
                retriable=True,
                repair_prompt="rewrite the candidate",
            )
        ],
    )

    attempt = make_gate_audit_attempt(
        report=report,
        candidates=[allowed, rejected],
        allowed_candidates=[allowed],
        index=0,
        result="retry_requested",
    )

    assert attempt == {
        "stage": "post_gradient",
        "index": 0,
        "result": "retry_requested",
        "targets": [
            {
                "name": "accepted",
                "uri": "viking://experiences/accepted.md",
                "outcome": "allowed",
                "decisions": [],
            },
            {
                "name": "rejected",
                "outcome": "rejected",
                "decisions": [
                    {
                        "gate": "quality",
                        "action": "reject",
                        "reason": "unsupported rule",
                        "retriable": True,
                    }
                ],
            },
        ],
    }


def test_policy_trainer_collects_gradient_and_plan_attempts_once():
    gradient_attempt = {"stage": "post_gradient", "index": 0}
    plan_attempt = {"stage": "post_plan", "index": 0}

    attempts = _collect_gate_attempts(
        analyses=[SimpleNamespace(metadata={"gate_attempts": [gradient_attempt]})],
        plans=[SimpleNamespace(metadata={"gate_attempts": [plan_attempt]})],
    )

    assert attempts == [gradient_attempt, plan_attempt]


@pytest.mark.asyncio
async def test_policy_optimizer_records_retry_as_one_gate_attempt(monkeypatch):
    candidate = SimpleNamespace(
        target_name="rejected", target_uri="viking://experiences/rejected.md"
    )
    captured = {}

    class FakeProvider:
        def __init__(self, **kwargs):
            del kwargs

        async def prefetch(self):
            return []

    class FakeIsolationHandler:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        def prepare_messages(self):
            return None

    class FakeExtractLoop:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def run(self):
            operations = SimpleNamespace(upsert_operations=[], delete_file_contents=[])
            captured["decision"] = await captured["post_validation_hook"](
                operations,
                0,
                latest_draft=operations,
            )
            return operations, []

    class RejectingRunner:
        async def filter_plan(self, items, *, analyses, policy_set):
            del analyses, policy_set
            assert items == [candidate]
            return [], GateReport(
                stage="post_plan",
                evaluated_count=1,
                rejected_count=1,
                decisions=[
                    GateDecision(
                        gate_name="quality",
                        action="reject",
                        reason="unsupported rule",
                        evidence={"target_name": "rejected"},
                        retriable=True,
                        repair_prompt="rewrite",
                    )
                ],
            )

    monkeypatch.setattr(policy_optimizer_module, "PatchMergeContextProvider", FakeProvider)
    monkeypatch.setattr(policy_optimizer_module, "MemoryIsolationHandler", FakeIsolationHandler)
    monkeypatch.setattr(policy_optimizer_module, "ExtractLoop", FakeExtractLoop)
    monkeypatch.setattr(policy_optimizer_module, "_required_file_uris", lambda *args: [])
    monkeypatch.setattr(
        policy_optimizer_module, "_gradient_to_merge_patch", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(policy_optimizer_module, "_seed_read_file_contents", lambda *args: None)
    monkeypatch.setattr(policy_optimizer_module, "_log_merge_input", lambda **kwargs: None)
    monkeypatch.setattr(
        policy_optimizer_module, "_operations_to_plan_items", lambda **kwargs: [candidate]
    )
    monkeypatch.setattr(
        policy_optimizer_module, "_remember_gated_plan_operations", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        PatchMergePolicyOptimizer,
        "_get_registry",
        lambda self: SimpleNamespace(),
    )
    monkeypatch.setattr(
        PatchMergePolicyOptimizer,
        "_get_schema",
        lambda self: SimpleNamespace(),
    )

    optimizer = PatchMergePolicyOptimizer(viking_fs=SimpleNamespace(), vlm=SimpleNamespace())
    context = PatchMergePolicyOptimizerContext(
        request_context=SimpleNamespace(),
        gate_runner=RejectingRunner(),
    )
    await optimizer._run_merge_extract_loop(
        gradients=[SimpleNamespace()],
        policy_set=SimpleNamespace(viking_fs=SimpleNamespace()),
        context=context,
    )

    assert captured["decision"].retry is True
    assert context.metadata["gate_attempts"] == [
        {
            "stage": "post_plan",
            "index": 0,
            "result": "retry_requested",
            "targets": [
                {
                    "name": "rejected",
                    "uri": "viking://experiences/rejected.md",
                    "outcome": "rejected",
                    "decisions": [
                        {
                            "gate": "quality",
                            "action": "reject",
                            "reason": "unsupported rule",
                            "retriable": True,
                        }
                    ],
                }
            ],
        }
    ]
