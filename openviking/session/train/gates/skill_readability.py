# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""ExperienceSkillReadabilityGate implementation."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ._shared import (
    _experience_constraint_and_trigger,
    _field_from_section,
    _markdown_section,
    _preview_text,
)
from .models import GateDecision, GateMode, GateTarget


@dataclass(slots=True)
class ExperienceSkillReadabilityGate:
    """Reject experiences that the skill loader cannot safely search/read."""

    mode: GateMode = "enforce"
    name: str = "experience_skill_readability"

    def applies_to(self, target: GateTarget) -> bool:
        return target.memory_type == "experiences" and target.after_content.strip() != ""

    async def evaluate(self, target: GateTarget) -> GateDecision | None:
        content, _ = _experience_constraint_and_trigger(target.after_content, target)
        missing_sections = [
            heading
            for heading in ("Situation", "Reminder", "Procedure", "Anti-pattern")
            if not _markdown_section(content, heading)
        ]
        situation = _markdown_section(content, "Situation")
        situation_fields = {
            "Applies when": _field_from_section(situation, "Applies when"),
            "Does not apply when": _field_from_section(situation, "Does not apply when"),
            "Evidence binding": (
                _field_from_section(situation, "Evidence binding")
                or _field_from_section(situation, "Source binding")
            ),
            "Decision boundary": _field_from_section(situation, "Decision boundary"),
        }
        missing_situation_fields = [
            field_name for field_name, value in situation_fields.items() if not value.strip()
        ]
        temporal_non_applicability = _temporal_non_applicability_terms(
            situation_fields["Does not apply when"]
        )
        if not missing_sections and not missing_situation_fields and not temporal_non_applicability:
            return None

        issues: list[str] = []
        repair_steps: list[str] = []
        if missing_sections:
            issues.append("missing sections: " + ", ".join(missing_sections))
            repair_steps.append(
                "include exactly these non-empty sections: `## Situation`, `## Reminder`, "
                "`## Procedure`, and `## Anti-pattern`"
            )
        if missing_situation_fields:
            issues.append("missing Situation fields: " + ", ".join(missing_situation_fields))
            repair_steps.append(
                "add non-empty `Applies when`, `Does not apply when`, `Evidence binding`, "
                "and `Decision boundary` bullets under `## Situation`"
            )
        if temporal_non_applicability:
            issues.append("temporal non-applicability: " + ", ".join(temporal_non_applicability))
            repair_steps.append(
                "replace `Does not apply when` with the closest task-pattern mismatch; do not "
                "describe an execution stage such as still reading/writing or before a final reply"
            )
        return GateDecision(
            gate_name=self.name,
            action="reject",
            reason="experience readability contract failed: " + "; ".join(issues),
            evidence={
                "target_name": target.target_name,
                "missing_sections": missing_sections,
                "missing_situation_fields": missing_situation_fields,
                "temporal_non_applicability": temporal_non_applicability,
                "situation_preview": _preview_text(situation, limit=500),
            },
            retriable=True,
            repair_prompt=(
                "Repair only these observed issues: " + "; ".join(repair_steps) + ". Put only "
                "the section bodies in the corresponding `situation`, `reminder`, `procedure`, "
                "and `anti_pattern` fields; the storage template adds the Markdown headings. "
                "Preserve already-correct, evidence-backed content and do not add trigger_code."
            ),
        )


_TEMPORAL_NON_APPLICABILITY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (term, re.compile(pattern, re.IGNORECASE))
    for term, pattern in (
        ("before_final_response", r"\b(before|until|not yet|prior to).{0,40}final[_ -]?response\b"),
        (
            "before_final_answer",
            r"\b(before|until|not yet|prior to).{0,40}final (answer|message|reply)\b",
        ),
        (
            "before_writes_complete",
            r"\b(before|until|not yet|prior to).{0,40}(writes?|mutations?|actions?).{0,30}(complete|done|finish)",
        ),
        (
            "still_reading_or_writing",
            r"\bstill (reading|retrieving|writing|executing|processing)\b",
        ),
        ("read_write_stage", r"\b(read|write|mutation|action)[-/ ]?stage\b"),
        ("not_final_response", r"\bnot (yet )?(at )?(the )?final[_ -]?response\b"),
        ("chinese_before_final_response", r"(最终回复前|最终回答前|还没到最终回复|尚未最终回复)"),
        ("chinese_still_reading_or_writing", r"(仍在|还在|正在).{0,8}(读取|查询|写入|执行|处理)"),
        (
            "chinese_before_write_complete",
            r"(写操作|修改|取消|更新).{0,12}(完成前|尚未完成|还没完成)",
        ),
    )
)


def _temporal_non_applicability_terms(text: str) -> list[str]:
    value = str(text or "")
    return [term for term, pattern in _TEMPORAL_NON_APPLICABILITY_PATTERNS if pattern.search(value)]
