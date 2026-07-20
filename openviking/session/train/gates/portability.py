# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""ExperiencePortabilityGate implementation."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ._shared import (
    _experience_constraint_and_trigger,
    _markdown_section,
)
from .models import GateDecision, GateMode, GateTarget


@dataclass(slots=True)
class ExperiencePortabilityGate:
    """Reject source-case literals that should be represented as runtime bindings."""

    mode: GateMode = "enforce"
    name: str = "experience_portability"

    def applies_to(self, target: GateTarget) -> bool:
        return target.memory_type == "experiences" and target.after_content.strip() != ""

    async def evaluate(self, target: GateTarget) -> GateDecision | None:
        content, _ = _experience_constraint_and_trigger(target.after_content, target)
        runtime_rule = "\n".join(
            (_markdown_section(content, "Reminder"), _markdown_section(content, "Procedure"))
        )
        auxiliary_artifacts = [
            match.group(0)
            for pattern in _MANDATORY_AUXILIARY_ARTIFACT_PATTERNS
            for match in pattern.finditer(runtime_rule)
        ]
        if auxiliary_artifacts:
            return GateDecision(
                gate_name=self.name,
                action="reject",
                reason="experience mandates an unrequested auxiliary artifact or fixed helper file",
                evidence={
                    "target_name": target.target_name,
                    "matches": auxiliary_artifacts[:5],
                },
                retriable=True,
                repair_prompt=(
                    "Keep the verification behavior but do not require a fixed helper filename, "
                    "visible intermediate file, or extra deliverable unless the runtime request or "
                    "tool contract explicitly requires it. Use an in-context requirement map or any "
                    "available scratch mechanism, and deliver only requested artifacts."
                ),
            )
        matches = [
            match.group(0)
            for pattern in _NON_PORTABLE_CASE_LITERAL_PATTERNS
            for match in pattern.finditer(content)
        ]
        if not matches:
            return None
        return GateDecision(
            gate_name=self.name,
            action="reject",
            reason="experience embeds source-case literals instead of semantic runtime bindings",
            evidence={
                "target_name": target.target_name,
                "matches": matches,
            },
            retriable=True,
            repair_prompt=(
                "Replace example numbers, date/month ranges, sheet/tab names, and helper script "
                "filenames with semantic roles read from the user request, source workbook, or "
                "available runtime tools. Preserve exact literals only when a cited authoritative "
                "runtime source makes them invariant across future cases."
            ),
        )


_NON_PORTABLE_CASE_LITERAL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\be\.g\.,?\s*[\"']?\d+(?:\.\d+)?(?:\s*[/,-]\s*\d+(?:\.\d+)?)*"
        r"(?:\s*(?:hours?|days?|weeks?|months?|years?|%))?\b",
        r"(?i)\bEvidence binding\s*:[^\n]*\b\d+\s*[-–]\s*\d+\s*"
        r"(?:days?|weeks?|months?|years?|%)\b",
        r"[\"'][^\"'\n]{1,80}[\"']\s+(?:sheet|tab|worksheet)\b",
        r"\b(?:sheet|tab|worksheet)\s+(?:named\s+)?[\"'][^\"'\n]{1,80}[\"']",
        r"\b[a-zA-Z_][a-zA-Z0-9_.-]*\.(?:py|sh|bash|command)\b",
        r"\be\.g\.,?\s*[\"'][^\"'\n]{0,80}\b[a-z]*[A-Z][a-zA-Z]*[A-Z][a-zA-Z]*"
        r"[^\"'\n]{0,80}[\"']",
        r"\b(?:January|February|March|April|May|June|July|August|September|October|"
        r"November|December)\b.{0,40}\bWeeks?\s*\d+(?:\s*[-–]\s*\d+)?\b",
    )
)


_MANDATORY_AUXILIARY_ARTIFACT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:write|create|save|generate)\s+(?:a\s+)?(?:visible\s+|mandatory\s+|"
        r"itemized\s+|intermediate\s+|temporary\s+|helper\s+|scratch\s+)+"
        r"(?:checklist\s+)?(?:file|document|artifact)\b",
        r"\b(?:must|always|do\s+not\s+skip|mandatory)\b.{0,100}"
        r"\b(?:intermediate|temporary|helper|scratch)\b.{0,40}\b(?:file|document|artifact)\b",
        r"(?<![a-z0-9])(?:[a-z0-9_.-]*(?:checklist|scratch|temporary|intermediate|helper|"
        r"requirements)[a-z0-9_.-]*)\.(?:md|txt|json|csv)(?![a-z0-9])",
    )
)
