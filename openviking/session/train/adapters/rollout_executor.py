# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""RolloutExecutor adapters."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from openviking.message import Message, TextPart
from openviking.session.train.domain import Case, ExecutionContext, ExperienceSet, Rollout
from openviking.telemetry import tracer
from openviking_cli.utils.config import get_openviking_config

PromptBuilder = Callable[[Case, ExperienceSet, ExecutionContext], str]


@dataclass(slots=True)
class SingleTurnLLMRolloutExecutor:
    """Execute each Case with one plain LLM call.

    This is a minimal RolloutExecutor for offline training bootstrap.  It does
    not run tools or a full agent loop; future agent-loop adapters can implement
    the same RolloutExecutor interface.
    """

    vlm: Any = None
    prompt_builder: PromptBuilder | None = None
    thinking: bool | None = None

    @tracer("train.rollout_executor.single_turn.execute", ignore_result=True, ignore_args=True)
    async def execute(
        self,
        cases: list[Case],
        policy_set: ExperienceSet,
        context: ExecutionContext,
    ) -> list[Rollout]:
        vlm = self.vlm or get_openviking_config().vlm
        rollouts: list[Rollout] = []
        for case in cases:
            prompt = self._build_prompt(case, policy_set, context)
            response = await vlm.get_completion_async(prompt=prompt, thinking=self.thinking)
            assistant_text = _response_text(response)
            rollouts.append(
                Rollout(
                    case=case,
                    messages=[
                        Message(
                            id=f"rollout-user-{uuid4().hex}",
                            role="user",
                            parts=[TextPart(text=prompt)],
                        ),
                        Message(
                            id=f"rollout-assistant-{uuid4().hex}",
                            role="assistant",
                            parts=[TextPart(text=assistant_text)],
                        ),
                    ],
                    policy_snapshot_id=context.policy_snapshot_id,
                )
            )
        return rollouts

    def _build_prompt(
        self,
        case: Case,
        policy_set: ExperienceSet,
        context: ExecutionContext,
    ) -> str:
        if self.prompt_builder is not None:
            return self.prompt_builder(case, policy_set, context)
        return default_single_turn_prompt(case, policy_set, context)


def default_single_turn_prompt(
    case: Case,
    policy_set: ExperienceSet,
    context: ExecutionContext,
) -> str:
    """Build a simple prompt containing policy experiences and case input."""

    experiences = "\n\n".join(
        f"### {policy.name} v{policy.version} [{policy.status}]\n{policy.content}"
        for policy in policy_set.policies
    )
    if not experiences:
        experiences = "(no experience policies available)"

    return "\n".join(
        [
            "You are executing an offline training case for OpenViking.",
            "Use the current experience policies when they are relevant.",
            "Return the best final answer/action for the case.",
            "",
            f"Policy snapshot: {context.policy_snapshot_id}",
            "",
            "# Experience Policies",
            experiences,
            "",
            "# Case",
            f"Name: {case.name}",
            f"Task signature: {case.task_signature}",
            "Input:",
            json.dumps(case.input, ensure_ascii=False, indent=2, sort_keys=True),
            "",
            "# Rubric",
            f"{case.rubric.name}: {case.rubric.description}",
            *[
                f"- {criterion.name} ({'required' if criterion.required else 'optional'}, "
                f"weight={criterion.weight}): {criterion.description}"
                for criterion in case.rubric.criteria
            ],
        ]
    )


def _response_text(response: Any) -> str:
    content = getattr(response, "content", None)
    if content is not None:
        return str(content)
    return str(response or "")
