# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""ExperienceLanguageBindingGate implementation."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ._shared import (
    _experience_constraint_and_trigger,
    _markdown_section,
)
from .models import GateDecision, GateMode, GateTarget


@dataclass(slots=True)
class ExperienceLanguageBindingGate:
    """Reject geography-to-language assumptions that can override user intent."""

    mode: GateMode = "enforce"
    name: str = "experience_language_binding"

    def applies_to(self, target: GateTarget) -> bool:
        return target.memory_type == "experiences" and target.after_content.strip() != ""

    async def evaluate(self, target: GateTarget) -> GateDecision | None:
        content, _ = _experience_constraint_and_trigger(target.after_content, target)
        runtime_rule = "\n".join(
            (_markdown_section(content, "Reminder"), _markdown_section(content, "Procedure"))
        )
        matches: list[str] = []
        for pattern in _GEOGRAPHY_LANGUAGE_INFERENCE_PATTERNS:
            matches.extend(
                match.group(0)
                for match in pattern.finditer(runtime_rule)
                if not _language_inference_is_prohibited(runtime_rule, match.start())
            )
        target_name = str(target.target_name or "")
        if (
            _LANGUAGE_AUDIENCE_TARGET_RE.search(target_name)
            and _GEOGRAPHY_AUDIENCE_RE.search(content)
            and not _LANGUAGE_INFERENCE_PROHIBITION_RE.search(target_name)
        ):
            matches.append(target_name)
        matches.extend(
            match.group(0)
            for match in _AUDIENCE_IMPLIES_LANGUAGE_RE.finditer(content)
            if not _language_inference_is_prohibited(content, match.start())
        )
        if not matches:
            return None
        return GateDecision(
            gate_name=self.name,
            action="reject",
            reason="experience infers output language from audience geography",
            evidence={
                "target_name": target.target_name,
                "matches": matches[:5],
            },
            retriable=True,
            repair_prompt=(
                "Bind output language to an explicit user language instruction. Geography or "
                "audience locale may change examples, spelling, or conventions, but must not "
                "alone select a language. If no explicit language is available, preserve the "
                "user's language choice or ask when necessary."
            ),
        )


_GEOGRAPHY_LANGUAGE_INFERENCE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE | re.DOTALL)
    for pattern in (
        r"\b(?:use|write|output|produce)\s+(?:all\s+content\s+)?(?:in\s+)?"
        r"(?:English|Chinese|Spanish|French|German|Japanese|Korean)\b.{0,100}"
        r"\b(?:audience|readers?|users?)\b",
        r"\b(?:audience|readers?|users?)\b.{0,100}\b(?:US|U\.S\.|UK|China|Chinese|"
        r"Japan|Japanese|Korea|Korean|France|French|Germany|German)[ -]?(?:based)?\b"
        r".{0,180}\b(?:use|using|write|output|produce|choose|select|prioritize)\b.{0,100}"
        r"\b(?:English|Chinese|Spanish|French|German|Japanese|Korean)\b",
        r"\b(?:English|Chinese|Spanish|French|German|Japanese|Korean)\s+for\s+"
        r"(?:US|U\.S\.|UK|China|Japan|Korea|France|Germany)[ -]?based\b",
        r"\b(?:US|U\.S\.|UK|China|Japan|Korea|France|Germany)[ -]?based\s+"
        r"(?:audience|readers?|users?)\b.{0,100}\b(?:use|write|output|produce)\b.{0,40}"
        r"\b(?:English|Chinese|Spanish|French|German|Japanese|Korean)\b",
    )
)


_LANGUAGE_AUDIENCE_TARGET_RE = re.compile(
    r"(?i)\b(?:match|select|choose|determine)[_ -]+(?:output[_ -]+)?language"
    r"[_ -]+(?:to|from|for|by)[_ -]+(?:audience|region|locale)\b"
)


_AUDIENCE_IMPLIES_LANGUAGE_RE = re.compile(
    r"(?i)\b(?:target\s+)?audience\b.{0,90}\b(?:implies?|indicates?|determines?|"
    r"suggests?)\b.{0,40}\b(?:a\s+)?(?:specific\s+)?language\b|"
    r"\blanguage[- ]implying\s+(?:target\s+)?audience\b|"
    r"\baudience\s+descriptions?\b.{0,50}\bimply\b.{0,30}\blanguage\b|"
    r"\b(?:determine|select|choose|infer)\b.{0,50}\b(?:output\s+)?language\b"
    r".{0,80}\b(?:from|based\s+on)\b.{0,30}\b(?:audience|locale|region)\b|"
    r"(?:根据|基于|按照).{0,20}(?:受众|读者|地区|地域|国家|区域).{0,20}"
    r"(?:确定|选择|决定|推断).{0,20}(?:输出)?语言|"
    r"(?:受众|读者|地区|地域|国家|区域).{0,20}(?:暗示|意味着|决定|对应).{0,12}语言"
)


_LANGUAGE_INFERENCE_PROHIBITION_RE = re.compile(
    r"(?i)\b(?:do\s+not|don't|never|must\s+not|should\s+not|avoid|prohibit)\b|"
    r"(?:不要|不得|禁止|避免)"
)


def _language_inference_is_prohibited(text: str, match_start: int) -> bool:
    """Return whether a matched inference is the object of a nearby prohibition."""

    prefix = text[max(0, match_start - 100) : match_start]
    prohibition = None
    for candidate in _LANGUAGE_INFERENCE_PROHIBITION_RE.finditer(prefix):
        prohibition = candidate
    if prohibition is None:
        return False
    between = prefix[prohibition.end() :]
    return "\n" not in between and len(between) <= 80


_GEOGRAPHY_AUDIENCE_RE = re.compile(
    r"(?i)\b(?:US|U\.S\.|UK|China|Japan|Korea|France|Germany)[ -]?(?:based)?\b|"
    r"\b(?:regional?|geograph(?:y|ic|ical)|locale)\s+(?:audience|readers?|users?)\b"
)
