# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for trajectory RootCauseGate."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openviking.session.memory.dataclass import ResolvedOperations
from openviking.session.memory.extract_loop import PostValidationRetryDecision
from openviking.session.train.components.root_cause_gate import RootCauseGate


class _SequenceVLM:
    model = "test-model"

    def __init__(self, responses):
        self.responses = list(responses)
        self.seen_calls = []

    async def get_completion_async(self, **kwargs):
        self.seen_calls.append(kwargs)
        if not self.responses:
            raise AssertionError("No fake VLM response left")
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_root_cause_gate_returns_generic_retry_decision():
    vlm = _SequenceVLM(
        [
            json.dumps(
                {
                    "pass": False,
                    "need_followup": True,
                    "root_cause_quality": "missing_source_binding",
                    "reason": "source binding is missing",
                    "followup_message": (
                        "Rewrite the complete JSON object with a source-bound ideal experience."
                    ),
                }
            )
        ]
    )
    gate = RootCauseGate(vlm=vlm, thinking=True, max_followups=2)
    latest_draft = SimpleNamespace(trajectories=[{"content": "surface level"}])

    decision = await gate(
        ResolvedOperations(upsert_operations=[], delete_file_contents=[], errors=[]),
        0,
        messages=[{"role": "user", "content": "extract"}],
        latest_draft=latest_draft,
    )

    assert isinstance(decision, PostValidationRetryDecision)
    assert decision.retry is True
    assert decision.discard is False
    assert decision.include_latest_draft is True
    assert "source-bound ideal experience" in decision.instruction
    assert len(vlm.seen_calls) == 1
    system_prompt = " ".join(vlm.seen_calls[0]["messages"][0]["content"].split())
    assert "authoritative outcome evidence" in system_prompt
    assert "Do not infer that separation merely because" in system_prompt
    assert "true action prerequisite" in system_prompt
    assert "Latest complete draft JSON" in vlm.seen_calls[0]["messages"][1]["content"]


@pytest.mark.asyncio
async def test_root_cause_gate_discards_on_final_failed_round():
    vlm = _SequenceVLM(
        [
            json.dumps(
                {
                    "pass": False,
                    "need_followup": True,
                    "root_cause_quality": "surface_level",
                    "reason": "still shallow",
                    "followup_message": "Rewrite again.",
                }
            )
        ]
    )
    gate = RootCauseGate(vlm=vlm, thinking=True, max_followups=2)
    latest_draft = SimpleNamespace(trajectories=[{"content": "still shallow"}])
    gate._followups_sent = 2

    decision = await gate(
        ResolvedOperations(upsert_operations=[], delete_file_contents=[], errors=[]),
        2,
        messages=[{"role": "user", "content": "extract"}],
        latest_draft=latest_draft,
    )

    assert isinstance(decision, PostValidationRetryDecision)
    assert decision.retry is False
    assert decision.discard is True


@pytest.mark.asyncio
async def test_root_cause_gate_noops_without_trajectory_draft():
    vlm = _SequenceVLM([])
    gate = RootCauseGate(vlm=vlm)

    decision = await gate(
        ResolvedOperations(upsert_operations=[], delete_file_contents=[], errors=[]),
        0,
        messages=[{"role": "user", "content": "extract"}],
        latest_draft=SimpleNamespace(preferences=[{"content": "ok"}]),
    )

    assert decision is None
    assert vlm.seen_calls == []
