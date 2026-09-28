# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Working Memory v2 document logic for OpenViking sessions.

Phase 2 of a commit generates / updates a structured 7-section Working
Memory document stored at ``archive_NNN/.overview.md``.

* First commit: call ``compression.ov_wm_v2`` with a plain completion unless
  partial-Turn retention also needs checkpoint summaries. In that case the
  same call uses ``create_working_memory`` and returns both products.
* Subsequent commits: call ``compression.ov_wm_v2_update`` with the
  ``update_working_memory`` tool to get a per-section decision plus any
  requested checkpoint summaries, then run section-level merge against the
  previous WM.

This module holds only pure/stateless helpers (string, dict and regex
transforms plus the tool schemas). It has no dependency on the ``Session``
object, so it can be imported and unit-tested in isolation. ``Session``
composes these functions during Phase 2 processing.
"""

import json
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from openviking.message import Message
from openviking.message.part import ContextPart, TextPart, ToolPart
from openviking.utils.token_estimation import estimate_text_tokens
from openviking_cli.utils import get_logger

if TYPE_CHECKING:
    from openviking.session.session import _CheckpointRequest

logger = get_logger(__name__)


# Match inline image transports emitted inside coding-agent tool output.
_INLINE_IMAGE_DATA_URL_RE = re.compile(
    r"data:(image/[a-z0-9.+-]+)(?:;[^;,\s\"'\\]+)*;base64,([a-z0-9+/_=-]+)",
    re.IGNORECASE,
)
_B64_JSON_RE = re.compile(
    r"(([\"'])b64_json\2\s*:\s*)([\"'])([a-z0-9+/_=-]+)\3",
    re.IGNORECASE,
)


def _inline_image_placeholder(mime: str, base64_chars: int) -> str:
    return f"[OpenViking inline image omitted: mime={mime}, base64_chars={base64_chars}]"


def redact_inline_images(text: str) -> str:
    def replace_data_url(match: re.Match[str]) -> str:
        return _inline_image_placeholder(match.group(1), len(match.group(2)))

    def replace_b64_json(match: re.Match[str]) -> str:
        quote = match.group(3)
        placeholder = _inline_image_placeholder("image/*", len(match.group(4)))
        return f"{match.group(1)}{quote}{placeholder}{quote}"

    return _B64_JSON_RE.sub(replace_b64_json, _INLINE_IMAGE_DATA_URL_RE.sub(replace_data_url, text))


def redact_inline_images_from_tool_outputs(messages: List[Message]) -> List[Message]:
    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolPart):
                part.tool_output = redact_inline_images(part.tool_output or "")
    return messages


def wm_debug(msg: str) -> None:
    """Log a WM v2 debug message via the standard logger."""
    logger.debug("wm_v2: %s", msg)


# =====================================================================
# Tool schemas and section definitions
# =====================================================================

WM_SEVEN_SECTIONS: List[str] = [
    "Session Title",
    "Current State",
    "Task & Goals",
    "Key Facts & Decisions",
    "Files & Context",
    "Errors & Corrections",
    "Open Issues",
]

_WM_SECTION_OP_SCHEMA: Dict[str, Any] = {
    "oneOf": [
        {
            "type": "object",
            "required": ["op"],
            "additionalProperties": False,
            "properties": {"op": {"type": "string", "enum": ["KEEP"]}},
        },
        {
            "type": "object",
            "required": ["op", "content"],
            "additionalProperties": False,
            "properties": {
                "op": {"type": "string", "enum": ["UPDATE"]},
                "content": {
                    "type": "string",
                    "description": (
                        "FULL replacement content for this section, markdown, "
                        "WITHOUT the '## <section>' header line."
                    ),
                },
            },
        },
        {
            "type": "object",
            "required": ["op", "items"],
            "additionalProperties": False,
            "properties": {
                "op": {"type": "string", "enum": ["APPEND"]},
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "New bullet-style items to append under the existing "
                        "section body. Omit heading / bullet markers; the "
                        "server renders each item as '- <item>'."
                    ),
                },
            },
        },
    ]
}

WM_UPDATE_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "update_working_memory",
        "description": (
            "Emit a per-section decision (KEEP / UPDATE / APPEND) for the "
            "7-section Working Memory document."
        ),
        "parameters": {
            "type": "object",
            "required": ["sections"],
            "additionalProperties": False,
            "properties": {
                "sections": {
                    "type": "object",
                    "required": list(WM_SEVEN_SECTIONS),
                    "additionalProperties": False,
                    "properties": dict.fromkeys(WM_SEVEN_SECTIONS, _WM_SECTION_OP_SCHEMA),
                },
                "checkpoint_summaries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "When checkpoint sources are present, one bounded cumulative "
                        "continuation summary per checkpoint_source index, in ascending "
                        "index order."
                    ),
                },
            },
        },
    },
}

WM_CREATE_WITH_CHECKPOINTS_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "create_working_memory",
        "description": (
            "Create the complete Working Memory and the requested checkpoint summaries "
            "from the same model pass."
        ),
        "parameters": {
            "type": "object",
            "required": ["working_memory", "checkpoint_summaries"],
            "additionalProperties": False,
            "properties": {
                "working_memory": {
                    "type": "string",
                    "description": "Complete 7-section Working Memory markdown.",
                },
                "checkpoint_summaries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "One bounded cumulative continuation summary per checkpoint_source "
                        "index, in ascending index order."
                    ),
                },
            },
        },
    },
}


# =====================================================================
# Message formatting for the WM model call
# =====================================================================


def format_message_for_wm(m: Message) -> str:
    """Format a single message for WM generation, including all parts.

    Includes TextPart, ToolPart (name + status + full output), and
    ContextPart so the WM LLM sees the complete conversation.
    """
    lines: List[str] = []
    for p in m.parts:
        if isinstance(p, TextPart) and p.text.strip():
            lines.append(p.text)
        elif isinstance(p, ToolPart) and p.tool_name:
            status = p.tool_status or "completed"
            output = redact_inline_images(p.tool_output or "")
            lines.append(f"[tool:{p.tool_name} ({status})] {output}")
        elif isinstance(p, ContextPart) and p.abstract:
            lines.append(f"[context] {p.abstract}")
    body = "\n".join(lines) if lines else "(no content)"
    return f"[{m.role}]: {body}"


def format_messages_for_wm(
    messages: List[Message],
    checkpoint_requests: List["_CheckpointRequest"],
) -> str:
    """Format WM input plus prior cumulative checkpoints using ordinal-only tags."""
    source_indexes: Dict[str, int] = {}
    for index, request in enumerate(checkpoint_requests):
        for message_id in request.source_message_ids:
            previous = source_indexes.setdefault(message_id, index)
            if previous != index:
                raise ValueError(
                    f"Checkpoint source message {message_id} belongs to multiple requests"
                )

    lines: List[str] = []
    for index, request in enumerate(checkpoint_requests):
        if not request.previous_checkpoint_abstract.strip():
            continue
        lines.extend(
            [
                f'<checkpoint_previous index="{index}">',
                request.previous_checkpoint_abstract.strip(),
                "</checkpoint_previous>",
            ]
        )
    open_index: Optional[int] = None
    for message in messages:
        index = source_indexes.get(message.id)
        if index != open_index:
            if open_index is not None:
                lines.append("</checkpoint_source>")
            if index is not None:
                lines.append(f'<checkpoint_source index="{index}">')
            open_index = index
        lines.append(format_message_for_wm(message))
    if open_index is not None:
        lines.append("</checkpoint_source>")
    return "\n".join(lines)


def checkpoint_prompt_instructions(request_count: int) -> str:
    if request_count <= 0:
        return ""
    return (
        "# CHECKPOINT OUTPUT\n\n"
        f"The session content contains checkpoint_source blocks indexed 0 through "
        f"{request_count - 1}. In the SAME tool call, return checkpoint_summaries "
        f"with exactly {request_count} strings in index order. For an index that "
        "also has checkpoint_previous, rewrite that previous summary together with "
        "its newly marked checkpoint_source block into one bounded cumulative "
        "continuation note. Without checkpoint_previous, summarize the marked block "
        "as the initial cumulative note. Preserve the assistant's intent, important "
        "tool actions and results, conclusions, corrections, and unfinished work; "
        "prefer newer facts when they supersede older ones, omit raw output bulk, "
        "and do not mention archiving, checkpointing, or this instruction. Never "
        "return only the new delta when checkpoint_previous is present."
    )


def parse_required_checkpoint_summaries(
    args: Dict[str, Any],
    request_count: int,
) -> tuple[str, ...]:
    raw = args.get("checkpoint_summaries")
    if not isinstance(raw, list):
        raise ValueError("tool_call arguments.checkpoint_summaries missing")
    if len(raw) != request_count or not all(isinstance(item, str) for item in raw):
        raise ValueError(
            f"tool_call checkpoint_summaries must contain exactly {request_count} strings"
        )
    return tuple(raw)


# =====================================================================
# Tool-call argument parsing / recovery
# =====================================================================


def parse_wm_sections(text: str) -> Dict[str, str]:
    """Parse an existing WM markdown into {header_line: body_text}.

    Header comparison is case-sensitive on purpose: the update path only
    uses this output to look up bodies by our own canonical headers.
    """
    sections: Dict[str, str] = {}
    current: Optional[str] = None
    buf: List[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = stripped
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return sections


_WM_SECTION_BULLET_THRESHOLD = 25
_WM_SECTION_TOKEN_THRESHOLD = 1500
_WM_OVERSIZED_APPEND_CAP = 5
_WM_CONSOLIDATION_SENTINEL = (
    "[⚠ CONSOLIDATION REQUIRED: Key Facts exceeds size limit. "
    "You MUST use UPDATE to merge and compress existing bullets "
    "before adding new facts.]"
)


def build_wm_section_reminders(overview: str) -> str:
    """Compute dynamic section-size warnings for the WM update prompt.

    Scans the current overview, counts bullets and estimates tokens for
    each section.  Returns an XML block that the prompt template can
    inject verbatim so the LLM knows which sections need consolidation.
    """
    if not overview:
        return ""
    sections = parse_wm_sections(overview)
    warnings: List[str] = []
    for header, body in sections.items():
        name = header.lstrip("#").strip()
        if name in _WM_APPEND_ONLY_SECTIONS:
            continue
        items = wm_extract_bullet_items(body)
        est_tokens = estimate_text_tokens(body)
        if len(items) > _WM_SECTION_BULLET_THRESHOLD or est_tokens > _WM_SECTION_TOKEN_THRESHOLD:
            warnings.append(
                f'WARNING: "{name}" has {len(items)} bullets '
                f"(~{est_tokens} tokens).\n"
                f"This section MUST be consolidated via UPDATE. Group "
                f"related facts by topic into category summaries. "
                f"Preserve names, dates, and exact values but merge "
                f"repetitive events into patterns.\n"
                f"Target: <={_WM_SECTION_BULLET_THRESHOLD} "
                f"bullets, <={_WM_SECTION_TOKEN_THRESHOLD} tokens."
            )
    if not warnings:
        return ""
    return "<section_size_warnings>\n" + "\n\n".join(warnings) + "\n</section_size_warnings>"


# Sections where server enforces APPEND-only regardless of what the LLM emits.
_WM_APPEND_ONLY_SECTIONS = frozenset(
    {
        "Errors & Corrections",
    }
)

# Very loose path-like token regex used to detect file paths that existed
# in prior Files & Context and MUST NOT silently disappear after UPDATE.
_WM_PATH_LIKE_RE = re.compile(
    r"(?:[\w./\\-]+\.(?:py|ts|tsx|js|jsx|md|yaml|yml|json|sh|ps1|cmd|bat|toml|ini|cfg|rs|go))"
    r"|(?:[a-zA-Z_][\w\-]*(?:/[a-zA-Z_][\w\-]*){1,})",
    re.IGNORECASE,
)

_WM_TITLE_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "by",
        "at",
        "from",
        "session",
        "title",
        "working",
        "memory",
        "plan",
        "plans",
        "notes",
        "note",
    }
)


def wm_recover_ops_from_raw(raw_str: str) -> Dict[str, Any]:
    """Best-effort regex recovery of per-section ops from a malformed
    tool_call arguments string.

    Used when OV's VLM backend wraps non-JSON tool-call args as
    ``{"raw": "..."}`` (typical when the LLM emits unescaped characters
    inside a string value, uses curly quotes, or emits a truncated JSON).
    Scans the raw text for each of the 7 fixed section names and their
    ``{"op": "KEEP|UPDATE|APPEND", ...}`` markers. Partial UPDATE
    content / APPEND items are tolerated; sections that cannot be found
    at all are simply omitted (the merge step will then default them to
    KEEP and preserve the prior content).

    Returns a partial ops dict (possibly fewer than 7 sections).
    """
    if not raw_str:
        return {}

    ops: Dict[str, Any] = {}
    names_alt = "|".join(re.escape(n) for n in WM_SEVEN_SECTIONS)

    # --- KEEP: "Name": {"op": "KEEP"} ---
    keep_re = re.compile(rf'"({names_alt})"\s*:\s*\{{\s*"op"\s*:\s*"KEEP"\s*\}}')
    for m in keep_re.finditer(raw_str):
        ops.setdefault(m.group(1), {"op": "KEEP"})

    # --- UPDATE: "Name": {"op": "UPDATE", "content": "..."} ---
    # Capture content non-greedily up to either:
    #   (a) a closing '"}' that ends the section, or
    #   (b) the start of the next section key (meaning content string was truncated).
    # DOTALL so newlines inside content don't end the match.
    update_re = re.compile(
        rf'"({names_alt})"\s*:\s*\{{\s*"op"\s*:\s*"UPDATE"\s*,\s*"content"\s*:\s*"'
        rf'((?:[^"\\]|\\.)*?)'
        rf'(?:"\s*\}}|(?="\s*,\s*"(?:' + names_alt + r')"))',
        re.DOTALL,
    )
    for m in update_re.finditer(raw_str):
        header = m.group(1)
        if header in ops:
            continue
        captured = m.group(2)
        try:
            content = json.loads('"' + captured + '"')
        except Exception:
            content = captured
        ops[header] = {"op": "UPDATE", "content": content}

    # --- APPEND: "Name": {"op": "APPEND", "items": [...]} ---
    # Tolerate truncated array (no closing ']').
    append_re = re.compile(
        rf'"({names_alt})"\s*:\s*\{{\s*"op"\s*:\s*"APPEND"\s*,\s*"items"\s*:\s*\['
        rf"([\s\S]*?)(?:\]|$)",
    )
    item_re = re.compile(r'"((?:[^"\\]|\\.)*)"', re.DOTALL)
    for m in append_re.finditer(raw_str):
        header = m.group(1)
        if header in ops:
            continue
        items_raw = m.group(2)
        items: List[str] = []
        for im in item_re.finditer(items_raw):
            captured = im.group(1)
            try:
                items.append(json.loads('"' + captured + '"'))
            except Exception:
                items.append(captured)
        ops[header] = {"op": "APPEND", "items": items}

    return ops


def wm_extract_bullet_items(text: str) -> List[str]:
    """Extract bullet-like items from a markdown section body.

    Recognizes ``- ...``, ``* ...``, ``1. ...``, ``2) ...`` lines, as well
    as plain non-bullet lines (treated as single items).
    """
    items: List[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = re.match(r"^(?:[-*]|\d+[\.)])\s+(.*)$", stripped)
        if m:
            item = m.group(1).strip()
        else:
            item = stripped
        if item:
            items.append(item)
    return items


# =====================================================================
# Per-section merge guards
# =====================================================================


def wm_enforce_append_only(header: str, op: Any, old_content: str) -> Dict[str, Any]:
    """Guard: force KEEP/APPEND semantics on APPEND-only sections.

    - KEEP and APPEND pass through.
    - UPDATE is demoted: its content is parsed for bullet items; any items
      that are not already present in the old body are re-emitted as APPEND
      items, so nothing from the LLM's rewrite is lost but nothing from
      the old body is dropped either.
    - None/unknown op -> KEEP.
    """
    if not isinstance(op, dict):
        return {"op": "KEEP"}
    op_name = (op.get("op") or "").upper()
    if op_name in ("KEEP", "APPEND"):
        return op
    if op_name != "UPDATE":
        return {"op": "KEEP"}

    new_content = (op.get("content") or "").strip()
    new_items = wm_extract_bullet_items(new_content)
    old_lower = (old_content or "").lower()
    fresh_items = []
    for it in new_items:
        key = it.strip("_* `").lower()
        if key and key not in old_lower:
            fresh_items.append(it)
    wm_debug(
        f"guard: section {header!r} UPDATE -> forced APPEND "
        f"(llm_items={len(new_items)}, fresh_after_dedup={len(fresh_items)})"
    )
    if not fresh_items:
        return {"op": "KEEP"}
    return {"op": "APPEND", "items": fresh_items}


_WM_KEY_FACTS_MIN_BULLET_RATIO = 0.15
_WM_KEY_FACTS_MIN_ANCHOR_COVERAGE = 0.70

_WM_ANCHOR_DATE_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b"
    r"|\b\d{1,2}\s+(?:January|February|March|April|May|June"
    r"|July|August|September|October|November|December)\s+\d{4}\b",
    re.IGNORECASE,
)
_WM_ANCHOR_NUMBER_RE = re.compile(
    r"\b\d+\s+(?:years?|months?|weeks?|days?|kids?|children"
    r"|hours?|miles?|times?|sessions?|rounds?|visits?"
    r"|dollars?|euros?|pounds?|bedrooms?|paintings?"
    r"|people|persons?)\b"
    r"|\$\d[\d,]*"
    r"|\b\d+\s+(?:AM|PM)\b",
    re.IGNORECASE,
)
_WM_ANCHOR_DECISION_RE = re.compile(
    r"\b(?:because|decided|chose|committed|agreed|resolved)\b",
    re.IGNORECASE,
)
_WM_ANCHOR_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "by",
        "at",
        "from",
        "is",
        "are",
        "was",
        "were",
        "has",
        "have",
        "had",
        "been",
        "be",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "shall",
        "this",
        "that",
        "these",
        "those",
        "not",
        "no",
        "but",
        "if",
        "then",
        "so",
        "as",
        "it",
        "its",
        "they",
        "their",
        "them",
        "she",
        "her",
        "he",
        "him",
        "his",
        "we",
        "our",
        "us",
        "you",
        "your",
        "who",
        "which",
        "what",
        "when",
        "where",
        "how",
        "why",
        "all",
        "each",
        "every",
        "both",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "than",
        "too",
        "very",
        "also",
        "just",
        "about",
        "after",
        "before",
        "between",
        "into",
        "through",
        "during",
        "again",
        "further",
        "once",
        "here",
        "there",
        "over",
        "under",
        "out",
        "up",
        "down",
        "off",
        "own",
        "same",
        "only",
        "new",
        "old",
        "key",
        "facts",
        "decisions",
        "session",
        "working",
        "memory",
    }
)


def extract_lexical_anchors(text: str) -> set:
    """Extract fact-preserving anchors: dates, numbers, proper nouns,
    decision markers."""
    anchors: set = set()
    for m in _WM_ANCHOR_DATE_RE.finditer(text):
        anchors.add(m.group().lower().strip())
    for m in _WM_ANCHOR_NUMBER_RE.finditer(text):
        anchors.add(m.group().lower().strip())
    for m in _WM_ANCHOR_DECISION_RE.finditer(text):
        anchors.add(m.group().lower().strip())
    for token in re.findall(r"\b[A-Z][a-zA-Z]{2,}\b", text):
        if token.lower() not in _WM_ANCHOR_STOPWORDS:
            anchors.add(token.lower())
    return anchors


def salvage_new_items_from_rejected_update(new_content: str, old_content: str) -> Dict[str, Any]:
    """When a consolidation UPDATE is rejected, salvage genuinely new
    items from the update content and APPEND them so we don't lose
    facts from the current round."""
    new_items = wm_extract_bullet_items(new_content)
    old_lower = (old_content or "").lower()
    fresh_items = []
    for it in new_items:
        key = it.strip("_* `").lower()
        if key and key not in old_lower:
            fresh_items.append(it)
    if fresh_items:
        wm_debug(f"guard: salvaged {len(fresh_items)} new items from rejected UPDATE")
        return {"op": "APPEND", "items": fresh_items}
    return {"op": "KEEP"}


def wm_enforce_key_facts_consolidation(op: Any, old_content: str) -> Dict[str, Any]:
    """Guard: allow controlled consolidation for Key Facts & Decisions.

    Layer 1 — reject trivially small UPDATEs (< 15% bullet count).
    Layer 2 — require >= 70% lexical anchor coverage.
    Rejection salvages genuinely new items from the rejected UPDATE
    via APPEND, so current-round facts are not silently lost.

    Anti-bloat: when Key Facts is already oversized (bullets or tokens
    exceed threshold), APPEND is throttled:
    - 1x~2x threshold → accept only genuinely new items (deduped),
      capped at _WM_OVERSIZED_APPEND_CAP
    - >2x threshold (emergency) → reject all normal facts, insert a
      single idempotent consolidation sentinel; on subsequent rounds
      the sentinel is already present so nothing is added (hard stop)
    """
    if not isinstance(op, dict):
        return {"op": "KEEP"}
    op_name = (op.get("op") or "").upper()
    if op_name == "KEEP":
        return op
    if op_name == "APPEND":
        old_items = wm_extract_bullet_items(old_content or "")
        est_tokens = estimate_text_tokens(old_content or "")
        bullet_over = len(old_items) > _WM_SECTION_BULLET_THRESHOLD
        token_over = est_tokens > _WM_SECTION_TOKEN_THRESHOLD
        if not bullet_over and not token_over:
            return op
        append_items = op.get("items") or []
        if not append_items:
            raw = (op.get("content") or "").strip()
            append_items = wm_extract_bullet_items(raw)
        append_items = [str(it) for it in append_items if it]
        old_lower = (old_content or "").lower()
        fresh = [it for it in append_items if it.strip("_* `").lower() not in old_lower]
        emergency = (
            len(old_items) > _WM_SECTION_BULLET_THRESHOLD * 2
            or est_tokens > _WM_SECTION_TOKEN_THRESHOLD * 2
        )
        if emergency:
            sentinel = _WM_CONSOLIDATION_SENTINEL
            if sentinel.lower() in old_lower:
                wm_debug(
                    f"guard: Key Facts APPEND blocked (emergency, "
                    f"sentinel already present): "
                    f"bullets={len(old_items)} est_tok={est_tokens} — "
                    f"dropped {len(fresh)} new item(s)"
                )
                return {"op": "KEEP"}
            wm_debug(
                f"guard: Key Facts APPEND blocked (emergency, "
                f"inserting sentinel): "
                f"bullets={len(old_items)} est_tok={est_tokens} — "
                f"dropped {len(fresh)} new item(s)"
            )
            return {"op": "APPEND", "items": [sentinel]}
        cap = _WM_OVERSIZED_APPEND_CAP
        accepted = fresh[:cap]
        wm_debug(
            f"guard: Key Facts APPEND throttled (oversized): "
            f"bullets={len(old_items)} est_tok={est_tokens} — "
            f"input={len(append_items)} deduped={len(fresh)} "
            f"accepted={len(accepted)} (cap={cap})"
        )
        if not accepted:
            return {"op": "KEEP"}
        return {"op": "APPEND", "items": accepted}
    if op_name != "UPDATE":
        return {"op": "KEEP"}

    new_content = (op.get("content") or "").strip()
    old_items = wm_extract_bullet_items(old_content or "")
    new_items = wm_extract_bullet_items(new_content)

    if not old_items:
        return op

    est_tokens = estimate_text_tokens(old_content or "")
    is_emergency = (
        len(old_items) > _WM_SECTION_BULLET_THRESHOLD * 2
        or est_tokens > _WM_SECTION_TOKEN_THRESHOLD * 2
    )

    # Layer 1: reject trivially small consolidation
    ratio = len(new_items) / len(old_items) if old_items else 1.0
    if ratio < _WM_KEY_FACTS_MIN_BULLET_RATIO:
        wm_debug(
            f"guard: Key Facts consolidation REJECTED (layer1): "
            f"new={len(new_items)} / old={len(old_items)} = "
            f"{ratio:.2%} < {_WM_KEY_FACTS_MIN_BULLET_RATIO:.0%}"
        )
        salvaged = salvage_new_items_from_rejected_update(new_content, old_content)
        if is_emergency and salvaged.get("op") == "APPEND":
            wm_debug("guard: suppressing salvage APPEND (emergency level)")
            return {"op": "KEEP"}
        return salvaged

    # Layer 2: lexical anchor coverage
    old_anchors = extract_lexical_anchors(old_content or "")
    if old_anchors:
        new_anchors = extract_lexical_anchors(new_content)
        covered = len(old_anchors & new_anchors)
        coverage = covered / len(old_anchors)
        if coverage < _WM_KEY_FACTS_MIN_ANCHOR_COVERAGE:
            wm_debug(
                f"guard: Key Facts consolidation REJECTED (layer2): "
                f"anchor coverage={coverage:.2%} "
                f"({covered}/{len(old_anchors)}) < "
                f"{_WM_KEY_FACTS_MIN_ANCHOR_COVERAGE:.0%}"
            )
            salvaged = salvage_new_items_from_rejected_update(new_content, old_content)
            if is_emergency and salvaged.get("op") == "APPEND":
                wm_debug("guard: suppressing salvage APPEND (emergency level)")
                return {"op": "KEEP"}
            return salvaged
        wm_debug(
            f"guard: Key Facts consolidation ACCEPTED: "
            f"bullets {len(old_items)}->{len(new_items)} "
            f"({ratio:.1%}), "
            f"anchors={coverage:.1%} ({covered}/{len(old_anchors)})"
        )
    else:
        wm_debug(
            f"guard: Key Facts consolidation ACCEPTED (no old anchors): "
            f"bullets {len(old_items)}->{len(new_items)}"
        )

    return op


def wm_enforce_files_no_regression(op: Any, old_content: str) -> Dict[str, Any]:
    """Guard: don't let a 'Files & Context' UPDATE drop file paths.

    If the LLM returns UPDATE whose content is missing one or more file
    paths that existed in the old content, reject the UPDATE. If the LLM
    introduced any new paths, surface them as an APPEND; otherwise KEEP.
    """
    if not isinstance(op, dict):
        return {"op": "KEEP"}
    op_name = (op.get("op") or "").upper()
    if op_name != "UPDATE":
        return op

    new_content = (op.get("content") or "").strip()
    old_paths = set(_WM_PATH_LIKE_RE.findall(old_content or ""))
    new_paths = set(_WM_PATH_LIKE_RE.findall(new_content))
    missing = {p for p in old_paths if p not in new_paths}
    if not missing:
        return op

    added_paths = new_paths - old_paths
    wm_debug(
        f"guard: 'Files & Context' UPDATE drops {len(missing)} paths "
        f"{sorted(missing)[:5]}; forcing KEEP (+ APPEND new paths="
        f"{len(added_paths)})"
    )
    if added_paths:
        # Preserve the old body as-is, then append the genuinely-new items
        # the LLM added (with a short rationale line if we can find one).
        new_items: List[str] = []
        for path in sorted(added_paths):
            # Try to pull the LLM's own phrasing for that path from new_content
            for line in new_content.splitlines():
                if path in line:
                    new_items.append(line.strip().lstrip("-*").strip())
                    break
            else:
                new_items.append(f"{path} (newly referenced)")
        return {"op": "APPEND", "items": new_items}
    return {"op": "KEEP"}


def wm_enforce_title_stability(op: Any, old_content: str) -> Dict[str, Any]:
    """Guard: reject Session Title UPDATE when it drifts too far.

    Heuristic: if the meaningful-word overlap between the old title and
    the proposed new title is 0, treat it as drift and fall back to KEEP.
    This catches the common failure where the LLM rewrites the title each
    round based on the latest delta instead of the overall session scope.
    """
    if not isinstance(op, dict):
        return {"op": "KEEP"}
    op_name = (op.get("op") or "").upper()
    if op_name != "UPDATE":
        return op

    new_content = (op.get("content") or "").strip()

    def meaningful_words(text: str) -> set:
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9\.]{2,}|[\d\.]+", text or "")
        return {t.lower() for t in tokens if t.lower() not in _WM_TITLE_STOPWORDS}

    old_w = meaningful_words(old_content)
    new_w = meaningful_words(new_content)

    # If the previous title was empty we have nothing to compare against.
    if not old_w:
        return op
    # If overlap >= 1 meaningful word, accept the rewording.
    if len(old_w & new_w) >= 1:
        return op
    wm_debug(
        f"guard: Session Title drift rejected "
        f"(old={old_content[:80]!r}, new={new_content[:80]!r}); KEEP"
    )
    return {"op": "KEEP"}


def wm_enforce_open_issues_resolved(op: Any, old_content: str) -> Dict[str, Any]:
    """Guard: don't let an Open Issues UPDATE silently drop items.

    Any bullet from the old body whose first 40 lowercase chars do not
    appear anywhere in the new content is considered silently dropped.
    We append those items back with a ``[silently dropped, restored]``
    marker so the caller can see the LLM's intent but no information is
    lost.
    """
    if not isinstance(op, dict):
        return op
    op_name = (op.get("op") or "").upper()
    if op_name != "UPDATE":
        return op

    new_content = (op.get("content") or "").strip()
    new_lower = new_content.lower()
    old_items = wm_extract_bullet_items(old_content or "")
    dropped: List[str] = []
    for it in old_items:
        if "[silently dropped, restored]" in it:
            continue
        snippet = it[:40].lower().strip("_* `").strip()
        if snippet and snippet not in new_lower:
            dropped.append(it)
    if not dropped:
        return op

    wm_debug(
        f"guard: Open Issues UPDATE silently dropped {len(dropped)} "
        f"items; restoring once (will not restore again if re-dropped)"
    )
    restored = "\n".join(f"- [silently dropped, restored] {it}" for it in dropped)
    merged = (new_content + ("\n" if new_content else "") + restored).strip()
    return {"op": "UPDATE", "content": merged}


def merge_wm_sections(old_wm: str, ops: Dict[str, Any]) -> str:
    """Merge LLM per-section ops into a new Working Memory document.

    ``ops`` is the schema-validated dict shaped like::

        {"Session Title":  {"op": "KEEP"},
         "Current State":  {"op": "UPDATE", "content": "..."},
         "Open Issues":    {"op": "APPEND", "items": ["...", "..."]}}

    Per-section server-side guards run BEFORE the op is applied:

    - ``Errors & Corrections`` is append-only; UPDATE is demoted to
      APPEND of only-new items.
    - ``Key Facts & Decisions`` uses a fact-preserving dual-threshold
      guard: UPDATE is accepted only if the consolidated content has
      >= 15% of old bullet count AND >= 70% lexical anchor coverage.
      Rejected UPDATEs fall back to APPEND (salvaging new facts)
      or KEEP if no new facts can be extracted.
    - ``Files & Context`` UPDATE that loses old file paths is rejected
      (KEEP + APPEND newly-added paths instead).
    - ``Session Title`` UPDATE with zero meaningful-word overlap against
      the prior title is rejected (KEEP instead).
    - ``Open Issues`` UPDATE that silently drops old items restores them
      with an explicit marker.

    Missing sections or unknown ops default to ``KEEP`` (the schema
    should prevent this, but we stay defensive so a buggy LLM or
    schema-loose backend cannot wipe out the prior WM).
    """
    wm_debug(
        f"merge_wm_sections entry old_wm={len(old_wm or '')}B "
        f"sections={list((ops or {}).keys())[:7]}"
    )
    old_sections = parse_wm_sections(old_wm)

    parts: List[str] = ["# Working Memory", ""]
    for header in WM_SEVEN_SECTIONS:
        full_header = f"## {header}"
        op = (ops or {}).get(header)
        old_content = old_sections.get(full_header, "").rstrip()

        # ---------- per-section guards ----------
        if old_content:
            if header == "Session Title":
                op = wm_enforce_title_stability(op, old_content)
            elif header == "Key Facts & Decisions":
                op = wm_enforce_key_facts_consolidation(op, old_content)
            elif header in _WM_APPEND_ONLY_SECTIONS:
                op = wm_enforce_append_only(header, op, old_content)
            elif header == "Files & Context":
                op = wm_enforce_files_no_regression(op, old_content)
            elif header == "Open Issues":
                op = wm_enforce_open_issues_resolved(op, old_content)
        # ----------------------------------------

        if op is None:
            new_content = old_content
        else:
            op_name = (op.get("op") or "").upper() if isinstance(op, dict) else ""
            if op_name == "KEEP":
                new_content = old_content
            elif op_name == "UPDATE":
                new_content = (op.get("content") or "").strip()
            elif op_name == "APPEND":
                items = op.get("items") or []
                bad_items = [s for s in items if not isinstance(s, str)]
                if bad_items:
                    logger.warning(
                        "wm_v2: dropped %d non-string APPEND item(s) in section %r: %s",
                        len(bad_items),
                        header,
                        [type(s).__name__ for s in bad_items],
                    )
                appended = "\n".join(
                    f"- {s.strip()}" for s in items if isinstance(s, str) and s.strip()
                )
                if old_content and appended:
                    new_content = f"{old_content}\n{appended}"
                else:
                    new_content = old_content or appended
            else:
                logger.warning(
                    "WM update: unknown op %r for section %r; keeping old content",
                    op,
                    header,
                )
                new_content = old_content

        parts.append(full_header)
        if new_content:
            parts.append(new_content)
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"
