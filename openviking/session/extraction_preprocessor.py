# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Input distillation helpers for Working Memory extraction.

This module is intentionally independent from ``Session`` wiring. The first
rollout can exercise it in unit tests and shadow evaluation before any runtime
path starts using the compact packet.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set

from openviking.message import Message
from openviking.message.part import ContextPart, TextPart, ToolPart

WM_SEVEN_SECTIONS: List[str] = [
    "Session Title",
    "Current State",
    "Task & Goals",
    "Key Facts & Decisions",
    "Files & Context",
    "Errors & Corrections",
    "Open Issues",
]

_SIGNAL_SECTIONS: List[str] = [
    "Current State",
    "Task & Goals",
    "Key Facts & Decisions",
    "Files & Context",
    "Errors & Corrections",
    "Open Issues",
]

_PATH_RE = re.compile(
    r"(?<![\w.-])(?:[A-Za-z]:[\\/]|/|\.{1,2}/)?"
    r"(?:[\w.-]+[\\/])+[\w.@%+=:,~/-]+\b"
)
_URL_RE = re.compile(r"https?://[^\s)>\]}\"']+")
_FUNCTION_RE = re.compile(r"\b[a-zA-Z_][\w]*\(\)")
_DATE_RE = re.compile(
    r"\b(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/\d{2,4}|"
    r"today|tomorrow|yesterday|deadline|due|计划|截止|今天|明天|昨天)\b",
    re.IGNORECASE,
)
_PREFERENCE_RE = re.compile(
    r"(?:prefer|preference|I like|I don't like|must|should|不要|别|偏好|喜欢|不喜欢|要求|必须)"
)
_CORRECTION_RE = re.compile(
    r"(?:actually|correction|correct|wrong|not that|不是|不对|纠正|更正|错了|改为|应该是)",
    re.IGNORECASE,
)
_ERROR_RE = re.compile(
    r"(?:error|exception|traceback|failed|failure|bug|fix|root cause|报错|错误|异常|失败|修复|根因)",
    re.IGNORECASE,
)
_OPEN_ISSUE_RE = re.compile(
    r"(?:todo|follow up|blocker|blocked|unresolved|open issue|question|待办|跟进|阻塞|未解决|问题|风险)",
    re.IGNORECASE,
)
_GOAL_RE = re.compile(
    r"(?:goal|objective|task|we need|let's|目标|任务|计划|实现|设计|方案)",
    re.IGNORECASE,
)
_PLUGIN_RE = re.compile(
    r"(?:plugin|extension|middleware|hook|adapter|connector|插件|扩展|中间件|适配器)",
    re.IGNORECASE,
)
_RECALL_RE = re.compile(
    r"(?:recall|retrieve|fetch.*memor|memory.*(?:lookup|search|query)|"
    r"召回|检索|回忆|查(?:询|找).*记忆|记忆.*(?:查|检|搜))",
    re.IGNORECASE,
)
_FALLBACK_RE = re.compile(
    r"(?:fallback|degraded?|graceful.*(?:exit|stop)|circuit.*(?:open|break)|"
    r"降级|回退|兜底|备用|后备)",
    re.IGNORECASE,
)
_COMPONENT_RE = re.compile(
    r"(?:openclaw|openviking|gateway|context.?engine|process.?manager|"
    r"auto.?recall|vikingbot|embedding|chunk|rag|vector)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PreprocessorOptions:
    """Configuration for compact packet construction."""

    max_span_tokens: int = 1200
    min_span_tokens: int = 200
    max_span_chars: int = 1600
    mmr_similarity_threshold: float = 0.72
    fallback_if_compact_ratio_above: float = 0.9
    expand_budget_on_risk: bool = True
    max_facts_total: int = 24
    min_full_tokens_for_compact: int = 600
    max_tool_output_chars: int = 300


@dataclass(frozen=True)
class SectionSignal:
    section: str
    text: str
    source_id: str
    source_index: int
    kind: str


@dataclass(frozen=True)
class SelectedSpan:
    source_id: str
    source_index: int
    role: str
    text: str
    score: float
    token_estimate: int


@dataclass(frozen=True)
class TokenEstimates:
    full_messages_tokens_est: int
    compact_packet_tokens_est: int
    saved_tokens_est: int


@dataclass
class CompactPacket:
    session_meta: Dict[str, object]
    section_signals: Dict[str, List[SectionSignal]]
    structured_facts: List[SectionSignal]
    selected_spans: List[SelectedSpan]
    risk_flags: List[str]
    token_estimates: TokenEstimates
    wm_update_view: str
    expanded_budget: bool = False
    fallback_reason: Optional[str] = None

    @property
    def should_fallback(self) -> bool:
        return bool(self.fallback_reason)


def estimate_tokens(text: str) -> int:
    """Estimate tokens using the repository's existing ceil(len / 4) heuristic."""

    if not text:
        return 0
    return -(-len(text) // 4)


def build_wm_compact_packet(
    messages: Sequence[Message],
    latest_overview: str = "",
    *,
    archive_uri: str = "",
    first_message_id: str = "",
    last_message_id: str = "",
    options: Optional[PreprocessorOptions] = None,
) -> CompactPacket:
    """Build a section-aware compact packet for WM v2 update prompts."""

    opts = options or PreprocessorOptions()
    normalized = [_normalize_message(m, idx, opts) for idx, m in enumerate(messages)]
    full_text = "\n".join(item["formatted"] for item in normalized)
    full_tokens = sum(int(getattr(m, "estimated_tokens", 0) or 0) for m in messages)
    if full_tokens <= 0:
        full_tokens = estimate_tokens(full_text)

    section_signals = _extract_section_signals(normalized)
    structured_facts = [
        signal
        for section in _SIGNAL_SECTIONS
        for signal in section_signals.get(section, [])
    ]

    # Cap structured facts per kind priority: error > path > url > correction >
    # preference > date > open_issue > function > goal > latest_message
    _KIND_PRIORITY = {
        "error": 0, "correction": 0, "fallback": 0, "url": 1, "path": 1,
        "function": 1, "component": 1, "plugin": 2, "recall": 2,
        "preference": 2, "date_or_plan": 2, "open_issue": 3, "goal": 4,
        "latest_message": 5,
    }
    if len(structured_facts) > opts.max_facts_total:
        structured_facts.sort(key=lambda f: _KIND_PRIORITY.get(f.kind, 9))
        structured_facts = structured_facts[: opts.max_facts_total]

    risk_flags = _detect_risk_flags(normalized, structured_facts)
    expanded_budget = bool(risk_flags and opts.expand_budget_on_risk)
    span_budget = max(opts.min_span_tokens, opts.max_span_tokens)
    if expanded_budget:
        span_budget = int(span_budget * 1.5)

    selected_spans = _select_spans(normalized, span_budget, opts)
    view = _render_wm_update_view(
        latest_overview=latest_overview,
        session_meta={
            "archive_uri": archive_uri,
            "first_message_id": first_message_id or (messages[0].id if messages else ""),
            "last_message_id": last_message_id or (messages[-1].id if messages else ""),
            "message_count": len(messages),
        },
        section_signals=section_signals,
        structured_facts=structured_facts,
        selected_spans=selected_spans,
        risk_flags=risk_flags,
        expanded_budget=expanded_budget,
    )
    compact_tokens = estimate_tokens(view)
    fallback_reason = None
    if not messages:
        fallback_reason = "no_messages"
    elif full_tokens < opts.min_full_tokens_for_compact:
        fallback_reason = "session_too_short"
    elif compact_tokens >= int(full_tokens * opts.fallback_if_compact_ratio_above):
        fallback_reason = "compact_not_smaller_enough"
    elif "failed_tool" in risk_flags and not any(
        span.source_index == item["index"] and item["has_tool"]
        for item in normalized
        for span in selected_spans
    ):
        fallback_reason = "failed_tool_not_selected"

    token_estimates = TokenEstimates(
        full_messages_tokens_est=full_tokens,
        compact_packet_tokens_est=compact_tokens,
        saved_tokens_est=max(0, full_tokens - compact_tokens),
    )
    session_meta = {
        "archive_uri": archive_uri,
        "first_message_id": first_message_id or (messages[0].id if messages else ""),
        "last_message_id": last_message_id or (messages[-1].id if messages else ""),
        "message_count": len(messages),
        "full_messages_tokens_est": full_tokens,
        "compact_packet_tokens_est": compact_tokens,
    }
    return CompactPacket(
        session_meta=session_meta,
        section_signals=section_signals,
        structured_facts=structured_facts,
        selected_spans=selected_spans,
        risk_flags=risk_flags,
        token_estimates=token_estimates,
        wm_update_view=view,
        expanded_budget=expanded_budget,
        fallback_reason=fallback_reason,
    )


def _normalize_message(message: Message, index: int, options: Optional[PreprocessorOptions] = None) -> Dict[str, object]:
    parts: List[str] = []
    has_tool = False
    failed_tool = False
    for part in message.parts:
        if isinstance(part, TextPart) and part.text.strip():
            parts.append(part.text.strip())
        elif isinstance(part, ContextPart) and part.abstract.strip():
            parts.append(f"[context] {part.abstract.strip()}")
        elif isinstance(part, ToolPart) and part.tool_name:
            has_tool = True
            status = part.tool_status or "completed"
            failed_tool = failed_tool or status == "error"
            input_text = ""
            if part.tool_input:
                input_text = json.dumps(part.tool_input, ensure_ascii=False, sort_keys=True)
            output = (part.tool_output or "").strip()
            max_out = options.max_tool_output_chars if options else 300
            if len(output) > max_out:
                output = output[:max_out].rstrip() + "\n...[tool output truncated]"
            stats = []
            if part.duration_ms is not None:
                stats.append(f"duration_ms={part.duration_ms}")
            if part.prompt_tokens is not None:
                stats.append(f"prompt_tokens={part.prompt_tokens}")
            if part.completion_tokens is not None:
                stats.append(f"completion_tokens={part.completion_tokens}")
            tool_lines = [f"[tool:{part.tool_name} ({status})]"]
            if input_text:
                tool_lines.append(f"input: {input_text}")
            if output:
                tool_lines.append(f"output: {output}")
            if stats:
                tool_lines.append("stats: " + ", ".join(stats))
            parts.append("\n".join(tool_lines))
    text = "\n".join(parts) if parts else "(no content)"
    if len(text) > 4000:
        text = text[:4000].rstrip() + "\n...[truncated]"
    return {
        "id": message.id,
        "index": index,
        "role": message.role,
        "text": text,
        "formatted": f"[{message.role} #{index} id={message.id}]: {text}",
        "has_tool": has_tool,
        "failed_tool": failed_tool,
    }


_METADATA_BLOCK_RE = re.compile(
    r"Sender\s*\(untrusted\s+metadata\)\s*:\s*```json\s*\{[^`]*```",
    re.IGNORECASE,
)


def _strip_metadata(text: str) -> str:
    """Remove OpenClaw metadata headers that can cause false regex matches."""
    cleaned = _METADATA_BLOCK_RE.sub("", text)
    # Also strip leading role tag like "[user]: " if it's the only thing left
    cleaned = re.sub(r"^\[(?:user|assistant|system)\]:\s*", "", cleaned).strip()
    return cleaned


def _extract_section_signals(
    normalized_messages: Sequence[Dict[str, object]]
) -> Dict[str, List[SectionSignal]]:
    signals: Dict[str, List[SectionSignal]] = {section: [] for section in _SIGNAL_SECTIONS}
    seen: Set[tuple] = set()
    for item in normalized_messages:
        text = str(item["text"])
        source_id = str(item["id"])
        source_index = int(item["index"])
        # Use stripped text for signal extraction to avoid false positives
        # from metadata headers, but keep original for the signal text.
        clean_text = _strip_metadata(text)
        _add_regex_signals(signals, seen, "Files & Context", "url", _URL_RE, text, source_id, source_index)
        _add_regex_signals(signals, seen, "Files & Context", "path", _PATH_RE, text, source_id, source_index)
        _add_regex_signals(signals, seen, "Files & Context", "function", _FUNCTION_RE, text, source_id, source_index)
        # Semantic matching uses clean_text to avoid false positives from
        # OpenClaw metadata headers like "Sender (untrusted metadata)".
        if _CORRECTION_RE.search(clean_text):
            _add_sentence_signal(signals, seen, "Errors & Corrections", "correction", clean_text, source_id, source_index)
        if _ERROR_RE.search(clean_text):
            _add_sentence_signal(signals, seen, "Errors & Corrections", "error", clean_text, source_id, source_index)
        if _OPEN_ISSUE_RE.search(clean_text):
            _add_sentence_signal(signals, seen, "Open Issues", "open_issue", clean_text, source_id, source_index)
        if _PREFERENCE_RE.search(clean_text):
            _add_sentence_signal(signals, seen, "Key Facts & Decisions", "preference", clean_text, source_id, source_index)
        if _DATE_RE.search(clean_text):
            _add_sentence_signal(signals, seen, "Key Facts & Decisions", "date_or_plan", clean_text, source_id, source_index)
        if _GOAL_RE.search(clean_text):
            _add_sentence_signal(signals, seen, "Task & Goals", "goal", clean_text, source_id, source_index)
        if _PLUGIN_RE.search(clean_text):
            _add_sentence_signal(signals, seen, "Files & Context", "plugin", clean_text, source_id, source_index)
        if _RECALL_RE.search(clean_text):
            _add_sentence_signal(signals, seen, "Files & Context", "recall", clean_text, source_id, source_index)
        if _FALLBACK_RE.search(clean_text):
            _add_sentence_signal(signals, seen, "Errors & Corrections", "fallback", clean_text, source_id, source_index)
        if _COMPONENT_RE.search(clean_text):
            _add_sentence_signal(signals, seen, "Files & Context", "component", clean_text, source_id, source_index)
    if normalized_messages:
        latest = normalized_messages[-1]
        _add_signal(
            signals,
            seen,
            SectionSignal(
                section="Current State",
                text=_compact_sentence(str(latest["text"])),
                source_id=str(latest["id"]),
                source_index=int(latest["index"]),
                kind="latest_message",
            ),
        )
    return signals


def _add_regex_signals(
    signals: Dict[str, List[SectionSignal]],
    seen: Set[tuple],
    section: str,
    kind: str,
    pattern: re.Pattern[str],
    text: str,
    source_id: str,
    source_index: int,
) -> None:
    for match in pattern.finditer(text):
        value = match.group(0).strip().rstrip(".,;:")
        if value:
            _add_signal(
                signals,
                seen,
                SectionSignal(section, value, source_id, source_index, kind),
            )


def _add_sentence_signal(
    signals: Dict[str, List[SectionSignal]],
    seen: Set[tuple],
    section: str,
    kind: str,
    text: str,
    source_id: str,
    source_index: int,
) -> None:
    _add_signal(
        signals,
        seen,
        SectionSignal(section, _compact_sentence(text), source_id, source_index, kind),
    )


def _add_signal(
    signals: Dict[str, List[SectionSignal]],
    seen: Set[tuple],
    signal: SectionSignal,
) -> None:
    key = (signal.section, signal.kind, signal.text)
    if key in seen:
        return
    seen.add(key)
    signals.setdefault(signal.section, []).append(signal)


def _compact_sentence(text: str, limit: int = 260) -> str:
    line = " ".join(text.strip().split())
    if len(line) <= limit:
        return line
    return line[:limit].rstrip() + "..."


def _detect_risk_flags(
    normalized_messages: Sequence[Dict[str, object]],
    structured_facts: Sequence[SectionSignal],
) -> List[str]:
    joined = "\n".join(str(item["text"]) for item in normalized_messages)
    flags: List[str] = []
    checks = [
        ("correction_or_negation", _CORRECTION_RE),
        ("explicit_preference", _PREFERENCE_RE),
        ("date_or_plan", _DATE_RE),
        ("error_or_fix", _ERROR_RE),
        ("open_issue", _OPEN_ISSUE_RE),
        ("fallback_or_degradation", _FALLBACK_RE),
        ("memory_recall", _RECALL_RE),
    ]
    for name, pattern in checks:
        if pattern.search(joined):
            flags.append(name)
    if any(bool(item["failed_tool"]) for item in normalized_messages):
        flags.append("failed_tool")
    if any(f.kind in {"path", "url", "function", "plugin", "component"} for f in structured_facts):
        flags.append("new_resource_reference")
    if len(structured_facts) >= 12:
        flags.append("dense_structured_facts")
    return flags


def _select_spans(
    normalized_messages: Sequence[Dict[str, object]],
    budget_tokens: int,
    options: PreprocessorOptions,
) -> List[SelectedSpan]:
    scored = []
    latest_user_index = _latest_role_index(normalized_messages, "user")
    for item in normalized_messages:
        text = str(item["text"])
        score = _score_message(item)
        if int(item["index"]) == latest_user_index:
            score += 4.0
        scored.append((score, item))
    scored.sort(key=lambda pair: (-pair[0], int(pair[1]["index"])))

    selected: List[SelectedSpan] = []
    used_tokens = 0
    selected_terms: List[Set[str]] = []
    for score, item in scored:
        text = _truncate_span(str(item["text"]), options.max_span_chars)
        token_estimate = estimate_tokens(text)
        if used_tokens + token_estimate > budget_tokens and selected:
            continue
        terms = _terms(text)
        if selected_terms and not bool(item["has_tool"]):
            max_similarity = max(_jaccard(terms, existing) for existing in selected_terms)
            if max_similarity > options.mmr_similarity_threshold:
                continue
        selected.append(
            SelectedSpan(
                source_id=str(item["id"]),
                source_index=int(item["index"]),
                role=str(item["role"]),
                text=text,
                score=score,
                token_estimate=token_estimate,
            )
        )
        selected_terms.append(terms)
        used_tokens += token_estimate
        if used_tokens >= budget_tokens:
            break

    selected.sort(key=lambda span: span.source_index)
    return selected


def _latest_role_index(messages: Sequence[Dict[str, object]], role: str) -> int:
    for item in reversed(messages):
        if item["role"] == role:
            return int(item["index"])
    return -1


def _score_message(item: Dict[str, object]) -> float:
    text = str(item["text"])
    score = 0.0
    if item["role"] == "user":
        score += 3.0
    if item["has_tool"]:
        score += 2.0
    if item["failed_tool"]:
        score += 5.0
    for pattern, weight in [
        (_PATH_RE, 2.0),
        (_URL_RE, 1.5),
        (_CORRECTION_RE, 4.0),
        (_ERROR_RE, 3.0),
        (_OPEN_ISSUE_RE, 2.5),
        (_PREFERENCE_RE, 2.0),
        (_FALLBACK_RE, 3.0),
        (_RECALL_RE, 2.0),
        (_PLUGIN_RE, 2.0),
        (_COMPONENT_RE, 2.0),
        (_DATE_RE, 1.5),
        (_GOAL_RE, 1.0),
    ]:
        if pattern.search(text):
            score += weight
    return score


def _paragraph_score(paragraph: str) -> float:
    """Score a paragraph for information density using the same regex signals."""
    score = 0.0
    for pattern, weight in [
        (_PATH_RE, 2.0),
        (_URL_RE, 1.5),
        (_CORRECTION_RE, 4.0),
        (_ERROR_RE, 3.0),
        (_OPEN_ISSUE_RE, 2.5),
        (_PREFERENCE_RE, 2.0),
        (_FALLBACK_RE, 3.0),
        (_RECALL_RE, 2.0),
        (_PLUGIN_RE, 2.0),
        (_COMPONENT_RE, 2.0),
        (_DATE_RE, 1.5),
        (_GOAL_RE, 1.0),
    ]:
        if pattern.search(paragraph):
            score += weight
    # Bonus for code-like content (backticks, indented blocks)
    if re.search(r"```|`[^`]+`|^\s{2,}\S", paragraph, re.MULTILINE):
        score += 1.5
    return score


def _truncate_span(text: str, max_chars: int) -> str:
    """Truncate text preserving the most information-dense paragraphs.

    Instead of naive head-truncation (which for a 75K-char message would keep
    only the first 2% and discard critical terms in the middle/end), this
    splits into paragraphs, scores each for signal density, and selects the
    highest-scoring paragraphs within the budget. The first and last paragraphs
    are always preserved for context and recency.
    """
    if len(text) <= max_chars:
        return text

    # Split into paragraphs (blank-line separated)
    raw_paragraphs = re.split(r"\n\s*\n", text)
    paragraphs = [p.strip() for p in raw_paragraphs if p.strip()]
    if not paragraphs:
        return text[:max_chars].rstrip() + "\n...[truncated]"

    n = len(paragraphs)
    if n <= 2:
        # Too few paragraphs to be selective; keep head but try to include tail
        head = paragraphs[0]
        tail = paragraphs[-1] if n > 1 else ""
        combined = head + ("\n\n" + tail if tail else "")
        if len(combined) <= max_chars:
            return combined
        return head[:max_chars].rstrip() + "\n...[truncated]"

    # Always preserve first and last paragraphs
    first, last = paragraphs[0], paragraphs[-1]
    budget = max_chars - len(first) - len(last) - len("\n\n...\n\n")
    if budget <= 0:
        # Not enough room; keep first + indicator
        return first[:max_chars - len("...[truncated]")].rstrip() + "\n...[truncated]"

    # Score and select middle paragraphs
    middle = paragraphs[1:-1]
    scored = [(i + 1, _paragraph_score(p), p) for i, p in enumerate(middle)]
    # Sort by score descending, then by original position ascending as tiebreaker
    scored.sort(key=lambda x: (-x[1], x[0]))

    selected_indices: set = set()
    used = 0
    for idx, _score, _para in scored:
        if used + len(_para) + 2 > budget:
            continue
        selected_indices.add(idx)
        used += len(_para) + 2  # +2 for "\n\n" separator

    # Rebuild in original order
    result_parts = [first]
    for i, para in enumerate(middle, start=1):
        if i in selected_indices:
            result_parts.append(para)
    result_parts.append(last)
    result = "\n\n".join(result_parts)

    # Safety: if the result is somehow still too long, fall back to head
    if len(result) > max_chars:
        return text[:max_chars].rstrip() + "\n...[truncated]"

    if len(selected_indices) < len(middle):
        result += "\n...[truncated: kept " + str(len(selected_indices) + 2) + "/" + str(n) + " paragraphs]"
    return result


def _terms(text: str) -> Set[str]:
    return {
        term.lower()
        for term in re.findall(r"[\w./:-]{3,}", text)
        if term.lower() not in {"the", "and", "with", "that", "this"}
    }


def _jaccard(left: Set[str], right: Set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _render_wm_update_view(
    *,
    latest_overview: str,
    session_meta: Dict[str, object],
    section_signals: Dict[str, List[SectionSignal]],
    structured_facts: Sequence[SectionSignal],
    selected_spans: Sequence[SelectedSpan],
    risk_flags: Sequence[str],
    expanded_budget: bool,
) -> str:
    lines: List[str] = [
        "# Compact Working Memory Update Packet",
        "",
        "Full raw archive is still stored outside this prompt and can be searched or expanded if needed.",
        "",
        "## Session Range",
    ]
    for key in ["archive_uri", "first_message_id", "last_message_id", "message_count"]:
        value = session_meta.get(key, "")
        lines.append(f"- {key}: {value}")

    lines.extend(["", "## Current Working Memory"])
    lines.append(latest_overview.strip() if latest_overview.strip() else "(none)")

    lines.extend(["", "## Risk Flags"])
    if risk_flags:
        for flag in risk_flags:
            lines.append(f"- {flag}")
        if expanded_budget:
            lines.append("- expanded_budget: true")
    else:
        lines.append("- none")

    lines.extend(["", "## Section Signals"])
    empty_sections: List[str] = []
    has_signals = False
    for section in WM_SEVEN_SECTIONS:
        if section == "Session Title":
            continue
        section_items = section_signals.get(section, [])
        if not section_items:
            empty_sections.append(section)
            continue
        has_signals = True
        lines.append(f"### {section}")
        for signal in section_items:
            lines.append(
                f"- [{signal.kind}] {signal.text} "
                f"(source: #{signal.source_index} {signal.source_id})"
            )
    if not has_signals:
        lines.append("- no section signals extracted")
    elif empty_sections:
        lines.append(f"- (no signals in: {', '.join(empty_sections)})")

    lines.extend(["", "## Structured Facts"])
    if structured_facts:
        for fact in structured_facts:
            lines.append(
                f"- {fact.section} / {fact.kind}: {fact.text} "
                f"(source: #{fact.source_index} {fact.source_id})"
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Selected Evidence Spans"])
    if selected_spans:
        for span in selected_spans:
            lines.append(
                f"### Span #{span.source_index} id={span.source_id} role={span.role} "
                f"score={span.score:.2f}"
            )
            lines.append(span.text)
            lines.append("")
    else:
        lines.append("- none")
    return "\n".join(lines).rstrip() + "\n"


__all__ = [
    "CompactPacket",
    "PreprocessorOptions",
    "SectionSignal",
    "SelectedSpan",
    "TokenEstimates",
    "build_wm_compact_packet",
    "estimate_tokens",
]
