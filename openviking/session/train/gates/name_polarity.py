# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""ExperienceNamePolarityGate implementation."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ._shared import (
    _experience_constraint_and_trigger,
    _markdown_section,
)
from .models import GateDecision, GateMode, GateTarget


@dataclass(slots=True)
class ExperienceNamePolarityGate:
    """Reject names that describe the opposite of the runtime behavior."""

    mode: GateMode = "enforce"
    name: str = "experience_name_polarity"

    def applies_to(self, target: GateTarget) -> bool:
        return target.memory_type == "experiences" and target.after_content.strip() != ""

    async def evaluate(self, target: GateTarget) -> GateDecision | None:
        target_name = str(target.target_name or "")
        content, _ = _experience_constraint_and_trigger(target.after_content, target)
        runtime_rule = "\n".join(
            (
                _markdown_section(content, "Reminder"),
                _markdown_section(content, "Procedure"),
                _markdown_section(content, "Anti-pattern"),
            )
        )
        contradiction = _experience_name_polarity_contradiction(target_name, runtime_rule)
        if contradiction is None:
            return None
        return GateDecision(
            gate_name=self.name,
            action="reject",
            reason="experience name describes behavior that its runtime rule prohibits",
            evidence={
                "target_name": target_name,
                "contradiction": contradiction,
            },
            retriable=True,
            repair_prompt=(
                "Rename this experience so its name describes the desired runtime behavior, "
                "not the anti-pattern that the body prohibits. Preserve the evidence-backed "
                "four-section body unless a wording change is required for consistency."
            ),
        )


def _experience_name_polarity_contradiction(
    target_name: str,
    runtime_rule: str,
) -> str | None:
    normalized_name = re.sub(r"[_-]+", " ", target_name).strip().lower()
    pairs = (
        (
            r"^skip\b",
            r"(?i)\b(?:do\s+not|don't|never|must\s+not|should\s+not)\s+skip\b|\bavoid\s+skipping\b",
        ),
        (
            r"^omit\b",
            r"(?i)\b(?:do\s+not|don't|never|must\s+not|should\s+not)\s+omit\b|\bavoid\s+omitting\b",
        ),
        (
            r"^ignore\b",
            r"(?i)\b(?:do\s+not|don't|never|must\s+not|should\s+not)\s+ignore\b|\bavoid\s+ignoring\b",
        ),
        (r"^跳过", r"(?:不要|不得|禁止|避免)跳过"),
        (r"^(?:忽略|省略)", r"(?:不要|不得|禁止|避免)(?:忽略|省略)"),
    )
    for name_pattern, rule_pattern in pairs:
        if re.search(name_pattern, normalized_name) and re.search(rule_pattern, runtime_rule):
            return f"name={target_name!r}; prohibited_action={name_pattern}"
    return None
