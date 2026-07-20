# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""ExperienceSpecificityGate implementation."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ._shared import (
    _experience_constraint_and_trigger,
    _markdown_section,
    _preview_text,
)
from .models import GateDecision, GateMode, GateTarget


@dataclass(slots=True)
class ExperienceSpecificityGate:
    """Reject generic requirement-checking slogans with no reusable discriminator."""

    mode: GateMode = "enforce"
    name: str = "experience_specificity"

    def applies_to(self, target: GateTarget) -> bool:
        return target.memory_type == "experiences" and target.after_content.strip() != ""

    async def evaluate(self, target: GateTarget) -> GateDecision | None:
        content, _ = _experience_constraint_and_trigger(target.after_content, target)
        independent_concerns = _independent_experience_concerns(content)
        if len(independent_concerns) > 1:
            return GateDecision(
                gate_name=self.name,
                action="reject",
                reason="experience merges independent failure-repair concerns",
                evidence={
                    "target_name": target.target_name,
                    "independent_concerns": independent_concerns,
                },
                retriable=True,
                repair_prompt=(
                    "Keep exactly one evidenced concern in this experience. Split concerns that "
                    "use different runtime evidence or corrective actions into separate narrow "
                    "experiences, or remove unsupported concerns."
                ),
            )
        situation = _markdown_section(content, "Situation")
        reminder = _markdown_section(content, "Reminder")
        procedure = _markdown_section(content, "Procedure")
        compound_actions = _compound_repair_actions(reminder)
        if len(compound_actions) >= 4:
            return GateDecision(
                gate_name=self.name,
                action="reject",
                reason="experience bundles several independent repairs into one reminder",
                evidence={
                    "target_name": target.target_name,
                    "repair_actions": compound_actions,
                    "reminder_preview": _preview_text(reminder, limit=400),
                },
                retriable=True,
                repair_prompt=(
                    "Keep one root failure and one behavior change. Split parallel repairs that "
                    "use different evidence or could fail independently into separate experiences. "
                    "A multi-step procedure is allowed only when every step implements the same "
                    "single corrective decision."
                ),
            )
        generic_signals = _generic_experience_signals(
            situation=situation,
            reminder=reminder,
            procedure=procedure,
            target_name=target.target_name,
        )
        if len(generic_signals) < 2:
            return None
        return GateDecision(
            gate_name=self.name,
            action="reject",
            reason="experience is a generic requirement-checking workflow",
            evidence={
                "target_name": target.target_name,
                "generic_signals": generic_signals,
                "reminder_preview": _preview_text(reminder, limit=300),
                "procedure_preview": _preview_text(procedure, limit=500),
            },
            retriable=True,
            repair_prompt=(
                "Replace the generic checklist with one evidenced failure pattern: name the "
                "runtime discriminator, the earliest decision boundary for that pattern, and "
                "the smallest corrective action. If no behavior more specific than checking "
                "all requirements is supported, output no changes."
            ),
        )


_GENERIC_EXPERIENCE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in (
        (
            "generic_requirement_reminder",
            r"\b(?:systematically\s+)?(?:check|verify|review|ensure|map)\s+(?:off\s+)?"
            r"(?:all|every|each)\s+(?:explicit\s+)?(?:user[- ]requested\s+|user\s+)?"
            r"requirements?\b",
        ),
        (
            "generic_requirement_checklist",
            r"\bcreate\s+a\s+checklist\s+from\s+(?:the\s+)?user(?:'s)?\s+"
            r"(?:explicit\s+)?requirements?\b",
        ),
        (
            "generic_full_requirement_checklist",
            r"\b(?:create|compile|build)\s+(?:a\s+|the\s+)?(?:full\s+|complete\s+|structured\s+)?"
            r"checklist\s+(?:of|from)\s+(?:all\s+|every\s+|explicit\s+|user\s+)*"
            r"requirements?\b",
        ),
        (
            "generic_required_item_checklist",
            r"\b(?:create|compile|build)\s+(?:a\s+|the\s+)?(?:full\s+|complete\s+|structured\s+)?"
            r"checklist\s+(?:of|from)\s+(?:all\s+)?(?:user[- ]specified\s+)?"
            r"(?:required\s+)?(?:content\s+)?(?:items?|topics?|requirements?)\b",
        ),
        (
            "generic_universal_required_content",
            r"\b(?:all|every|each)\s+(?:explicit\s+)?(?:user[- ]specified\s+|user\s+)?"
            r"required\s+content\s+items?\b",
        ),
        (
            "generic_document_required_content_scope",
            r"\bcreat(?:e|ing)\s+(?:a\s+)?documents?\b.{0,100}"
            r"\bexplicit\s+(?:list\s+of\s+)?required\s+content\s+items?\b",
        ),
        (
            "generic_mark_implemented",
            r"\bfor\s+each\s+requirement.{0,80}\bmark(?:ed)?\s+as\s+implemented\b",
        ),
        (
            "generic_requirement_placeholder",
            r"\bfor\s+each\s+requirement.{0,100}\b(?:placeholder|section|content element)\b",
        ),
        (
            "generic_requirement_inventory",
            r"\b(?:extract|list|enumerate)(?:\s+and\s+(?:extract|list|enumerate))?\s+"
            r"(?:all|every|each)\s+"
            r"(?:explicit\s+)?(?:user[- ]requested\s+|user\s+)?requirements?\b",
        ),
        (
            "generic_content_requirement_inventory",
            r"\b(?:inventory|extract|list|map)\s+(?:all|every|each)\s+"
            r"(?:explicit\s+)?(?:user[- ]specified\s+|user\s+)?"
            r"(?:(?:slide|document|strategy|deliverable)\s+)?content\s+requirements?\b",
        ),
        (
            "generic_universal_content_requirement_scope",
            r"\b(?:all|every|each)\s+(?:explicit\s+)?(?:user[- ]specified\s+|user\s+)?"
            r"(?:(?:slide|document|strategy|deliverable)\s+)?content\s+requirements?\b",
        ),
        (
            "generic_universal_required_topics",
            r"\b(?:all|every|each)\s+(?:explicit\s+)?required\s+topics?\b",
        ),
        (
            "generic_compile_review_checklist",
            r"\bcompile(?:\s+and\s+(?:review|verify|check))?\s+(?:a\s+|the\s+)?"
            r"(?:full\s+|complete\s+)?checklist\s+of\s+(?:all\s+|every\s+)?"
            r"(?:explicit\s+)?requirements?\b",
        ),
        (
            "generic_universal_requirement_scope",
            r"\b(?:all|every|each)\s+(?:explicit\s+)?(?:user[- ]specified\s+|user\s+)?"
            r"requirements?\b(?!\s+(?:group|category|cluster|section|region)\b)",
        ),
        (
            "generic_requirement_mapping",
            r"\bmap\s+(?:all|every|each)\s+(?:explicit\s+)?(?:user[- ]specified\s+|user\s+)?"
            r"requirements?\b(?!\s+(?:group|category|cluster|section|region)\b)",
        ),
        (
            "generic_line_by_line_requirement_check",
            r"\b(?:check|verify|compare)\s+(?:all|every|each)?\s*(?:explicit\s+)?"
            r"(?:user[- ]specified\s+|user\s+)?requirements?\s+"
            r"(?:line[- ]by[- ]line|one[- ]by[- ]one)\b",
        ),
        (
            "generic_cross_verify_requirements",
            r"\bcross[- ]verify\s+(?:each|all|every)\s+requirements?\b",
        ),
        (
            "generic_compliance_slogan",
            r"\b(?:ensure|verify)\s+(?:full|complete)\s+compliance\b",
        ),
        ("generic_review_slogan", r"\breview\s+(?:it\s+)?(?:carefully|thoroughly)\b"),
        ("chinese_generic_check_all", r"(逐项|逐一)?(检查|核对|验证).{0,12}(全部|所有)(要求|需求)"),
        ("chinese_generic_compliance", r"确保.{0,8}(完全|全部)(合规|符合要求)"),
        (
            "generic_document_requirement_scope",
            r"\bcreat(?:e|ing)\s+(?:a\s+)?(?:document|artifact|deliverable|structured content)"
            r".{0,100}\b(?:explicit|enumerated|listed)\b.{0,40}\brequirements?\b",
        ),
        (
            "generic_artifact_requirement_scope",
            r"\b(?:programmatically\s+)?(?:generat(?:e|ing)|creat(?:e|ing)|finaliz(?:e|ing))\s+"
            r"(?:a\s+)?(?:structured\s+)?(?:deliverable\s+)?"
            r"(?:artifacts?|documents?|deliverables?)\b.{0,100}"
            r"\b(?:explicit|enumerated|listed)\b.{0,40}\brequirements?\b",
        ),
        (
            "generic_component_inventory",
            r"\b(?:extract|list|enumerate)(?:\s+and\s+(?:extract|list|enumerate))?\s+"
            r"(?:all|every|each)\s+(?:explicitly\s+required\s+|user[- ]specified\s+|"
            r"user[- ]requested\s+)?(?:tables?|sheets?|sections?|components?)\b",
        ),
        (
            "generic_component_checklist",
            r"\bchecklist\s+(?:of|from|containing)\s+(?:all|every|each)?\s*"
            r"(?:explicitly\s+required\s+|user[- ]specified\s+|user[- ]requested\s+)?"
            r"(?:tables?|sheets?|sections?|components?)\b",
        ),
    )
)


def _generic_experience_signals(
    *,
    situation: str,
    reminder: str,
    procedure: str,
    target_name: str = "",
) -> list[str]:
    text = "\n".join((situation, reminder, procedure))
    signals = [name for name, pattern in _GENERIC_EXPERIENCE_PATTERNS if pattern.search(text)]
    if _GENERIC_EXPERIENCE_NAME_RE.search(str(target_name or "")):
        signals.append("generic_requirement_name")
    return signals


_GENERIC_EXPERIENCE_NAME_RE = re.compile(
    r"(?i)\b(?:validate|verify|check|address|cover|inventory|list|map)[_ -]+"
    r"(?:(?:all|every|each|explicit|required|user(?:[_ -]+specified)?)[_ -]+)*"
    r"(?:(?:slide|document|strategy|deliverable)[_ -]+)?"
    r"(?:requirements?|topics?|content[_ -]+requirements?)(?=$|[_ -])"
)


_INDEPENDENT_EXPERIENCE_CONCERN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "internal_output_cleanup",
        re.compile(
            r"(?is)(?:internal\s+(?:tools?|workflows?|metadata|paths?|process)|"
            r"内部(?:工具|流程|元数据|路径|文件)).{0,160}(?:final|user-facing|最终|用户)",
        ),
    ),
    (
        "source_reference_completeness",
        re.compile(
            r"(?is)(?:sources?|references?|citations?|supporting materials|来源|参考文献|引用)"
            r".{0,120}(?:include|provide|verify|missing|包含|提供|核对|缺失)",
        ),
    ),
)


_COMPOUND_REPAIR_ACTION_RE = re.compile(
    r"(?i)\b(?:define|distinguish|differentiate|address|cover|compare|include|document|"
    r"classify|explain|summarize|cite|calculate|reconcile|label|disclose|map|validate|"
    r"verify|confirm|note)\b"
)


def _compound_repair_actions(reminder: str) -> list[str]:
    """Find parallel corrective verbs compressed into the Reminder section."""

    return list(
        dict.fromkeys(
            match.group(0).lower() for match in _COMPOUND_REPAIR_ACTION_RE.finditer(reminder)
        )
    )


def _independent_experience_concerns(content: str) -> list[str]:
    value = str(content or "")
    return [
        name for name, pattern in _INDEPENDENT_EXPERIENCE_CONCERN_PATTERNS if pattern.search(value)
    ]
