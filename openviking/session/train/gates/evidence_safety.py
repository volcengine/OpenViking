# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""ExperienceEvidenceSafetyGate implementation."""

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
class ExperienceEvidenceSafetyGate:
    """Reject experience rules that turn missing evidence into invented content."""

    mode: GateMode = "enforce"
    name: str = "experience_evidence_safety"

    def applies_to(self, target: GateTarget) -> bool:
        return target.memory_type == "experiences" and target.after_content.strip() != ""

    async def evaluate(self, target: GateTarget) -> GateDecision | None:
        content, _ = _experience_constraint_and_trigger(target.after_content, target)
        runtime_rule = "\n".join(
            (_markdown_section(content, "Reminder"), _markdown_section(content, "Procedure"))
        )
        unsafe_matches = [
            match.group(0)
            for pattern in _UNSUPPORTED_CONTENT_FILL_PATTERNS
            for match in pattern.finditer(runtime_rule)
        ]
        if unsafe_matches and not _EXPLICIT_PLACEHOLDER_PERMISSION_RE.search(content):
            return GateDecision(
                gate_name=self.name,
                action="reject",
                reason=(
                    "experience permits filling missing evidence with assumptions or placeholders"
                ),
                evidence={
                    "target_name": target.target_name,
                    "matches": unsafe_matches,
                },
                retriable=True,
                repair_prompt=(
                    "Remove any instruction to guess, assume, or insert placeholders for missing "
                    "required evidence unless the user explicitly permits that behavior. Prefer "
                    "an explicit unavailable value, a clarification request, or a disclosed "
                    "limitation."
                ),
            )
        restatement_matches = [
            match.group(0)
            for pattern in _UNREQUESTED_CONTEXT_RESTATEMENT_PATTERNS
            for match in pattern.finditer(runtime_rule)
        ]
        if restatement_matches and not _EXPLICIT_CONTEXT_RESTATEMENT_REQUEST_RE.search(content):
            return GateDecision(
                gate_name=self.name,
                action="reject",
                reason="experience turns user-provided assumptions or exclusions into unrequested output",
                evidence={
                    "target_name": target.target_name,
                    "matches": restatement_matches[:5],
                },
                retriable=True,
                repair_prompt=(
                    "Use assumptions, exclusions, and pre-handled topics as reasoning constraints. "
                    "Do not require copying or restating them in the deliverable unless the runtime "
                    "request explicitly asks for that visible content."
                ),
            )
        if (
            _FIXED_CAPACITY_ARTIFACT_RE.search(content)
            and _DENSITY_AS_OVERFLOW_RE.search(runtime_rule)
            and not _EXPLICIT_OVERFLOW_RESOLUTION_RE.search(runtime_rule)
        ):
            return GateDecision(
                gate_name=self.name,
                action="reject",
                reason="experience resolves fixed-capacity conflicts by cramming content",
                evidence={
                    "target_name": target.target_name,
                    "runtime_rule_preview": _preview_text(runtime_rule, limit=500),
                },
                retriable=True,
                repair_prompt=(
                    "Keep the requirement-to-region mapping, but preserve readability. When all "
                    "required content cannot fit at a readable density, follow an explicit priority, "
                    "surface the conflict, or request a scope/layout decision instead of shrinking "
                    "text or silently packing dense nested content."
                ),
            )
        scope_matches = [
            match.group(0)
            for pattern in _UNSUPPORTED_SCOPE_EXPANSION_PATTERNS
            for match in pattern.finditer(runtime_rule)
        ]
        if not scope_matches:
            return None
        return GateDecision(
            gate_name=self.name,
            action="reject",
            reason="experience turns an unrequested convention into a mandatory requirement",
            evidence={
                "target_name": target.target_name,
                "matches": scope_matches,
            },
            retriable=True,
            repair_prompt=(
                "Do not make content mandatory merely because it is conventional for the artifact "
                "type. Bind the requirement to the user request, an authoritative source, or an "
                "observable task schema; otherwise make it optional or output no experience."
            ),
        )


_UNSUPPORTED_CONTENT_FILL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bif\b[^.\n]{0,60}\b(?:unsure|uncertain|unknown|missing|unavailable)\b"
        r"[^.\n]{0,120}\b(?:add|insert|use|fill)\b[^.\n]{0,60}\bplaceholders?\b",
        r"\bmake\s+(?:a\s+)?(?:reasonable\s+)?(?:explicit\s+)?assumptions?\b",
        r"(?:不确定|缺失|未知|无法获取).{0,60}(?:填入|使用|添加).{0,30}(?:占位符|假设值)",
    )
)


_UNSUPPORTED_SCOPE_EXPANSION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:require|requires|required|must|include|add)\b[^.\n]{0,160}"
        r"\b(?:even\s+(?:when|if)|although)\b[^.\n]{0,100}"
        r"\b(?:not\s+explicitly\s+(?:listed|required|requested|specified)|not\s+requested)\b",
        r"\bdo\s+not\s+skip\b[^.\n]{0,160}\b(?:just\s+because|even\s+if)\b"
        r"[^.\n]{0,100}\bnot\s+explicitly\s+(?:listed|required|requested|specified)\b",
        r"(?:即使|虽然).{0,80}(?:用户)?(?:未|没有)(?:明确)?(?:要求|列出|指定).{0,80}"
        r"(?:也必须|仍需|仍然要|必须添加|必须包含)",
    )
)


_EXPLICIT_PLACEHOLDER_PERMISSION_RE = re.compile(
    r"(?i)\buser\s+(?:explicitly\s+)?(?:allows?|permits?|requests?)\b.{0,80}"
    r"\b(?:placeholders?|assumptions?|estimated values?)\b|"
    r"用户.{0,20}(?:明确)?(?:允许|要求).{0,30}(?:占位符|假设值|估算值)"
)


_UNREQUESTED_CONTEXT_RESTATEMENT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:restate|copy|repeat|place|add|include)\b[^.\n]{0,120}"
        r"\b(?:assumptions?|exclusions?|pre[- ]handled topics?)\b[^.\n]{0,100}"
        r"\b(?:introduction|scope section|document|deliverable|report|memo|final output)\b",
        r"\b(?:assumptions?|exclusions?|pre[- ]handled topics?)\b[^.\n]{0,100}"
        r"\b(?:restate|copy|repeat|place|add|include)\b[^.\n]{0,100}"
        r"\b(?:introduction|scope section|document|deliverable|report|memo|final output)\b",
    )
)


_EXPLICIT_CONTEXT_RESTATEMENT_REQUEST_RE = re.compile(
    r"(?is)\buser\b.{0,40}\b(?:asks?|requires?|instructs?|explicitly\s+requests?)\b"
    r".{0,100}\b(?:restate|copy|repeat|place|add|include|show)\b.{0,100}"
    r"\b(?:assumptions?|exclusions?|pre[- ]handled topics?)\b|"
    r"用户.{0,30}(?:明确)?(?:要求|请求|指示).{0,80}(?:复述|列出|包含|展示).{0,80}"
    r"(?:假设|排除项|已处理事项)"
)


_FIXED_CAPACITY_ARTIFACT_RE = re.compile(
    r"(?i)\b(?:single[- ]slide|one[- ]slide|one[- ]page|single[- ]page|fixed[- ]capacity|"
    r"fixed[- ]size|space[- ]constrained)\b|(?:单页|单张|一页|固定容量|空间受限)"
)


_DENSITY_AS_OVERFLOW_RE = re.compile(
    r"(?i)\b(?:dense formatting|nested bullet points?|smaller fonts?|shrink(?:ing)?\s+(?:the\s+)?"
    r"(?:font|text|content)|pack(?:ing)?\s+(?:the\s+)?content|condense everything)\b|"
    r"(?:密集排版|嵌套项目符号|缩小字体|压缩全部内容|塞入)"
)


_EXPLICIT_OVERFLOW_RESOLUTION_RE = re.compile(
    r"(?i)\b(?:readab(?:le|ility)|overflow|priorit(?:y|ize|ise|ization)|trade[- ]?off|"
    r"clarif(?:y|ication)|ask\s+(?:the\s+)?user|user permission|scope decision)\b|"
    r"(?:可读性|溢出|优先级|取舍|澄清|询问用户|用户确认|范围决策)"
)
