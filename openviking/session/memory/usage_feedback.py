# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Score how recalled memories affected a session and persist the running value.

A client reports each memory it put in front of the agent as a ``ContextPart``
(URI only). At commit, one LLM call judges every reported memory against the
archived trajectory: +1 helped, 0 unused, -1 harmed. The verdicts accumulate
into ``MEMORY_FIELDS.usage_feedback`` of the memory file; the body and the
memory version are left untouched because this is bookkeeping, not content.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional

from openviking.message import Message
from openviking.message.part import ContextPart
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.utils.json_parser import extract_json_content
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

USAGE_FEEDBACK_FIELD = "usage_feedback"
MAX_CANDIDATES = 30
MAX_TRAJECTORY_CHARS = 60_000
MAX_MEMORY_CHARS = 2_000
MAX_REASON_CHARS = 200
_SCORES = (-1, 0, 1)


@dataclass(frozen=True)
class Verdict:
    uri: str
    score: int
    reason: str


def collect_candidate_uris(messages: List[Message], *, limit: int = MAX_CANDIDATES) -> List[str]:
    """Memory URIs referenced by ContextParts, most frequent first, ties by first sight."""
    counts: Counter[str] = Counter()
    for message in messages:
        for part in message.parts:
            if (
                isinstance(part, ContextPart)
                and part.context_type == "memory"
                and "/memories/" in part.uri
            ):
                counts[part.uri] += 1
    # Counter preserves insertion order, and sorted() is stable.
    return sorted(counts, key=lambda uri: -counts[uri])[:limit]


def render_trajectory(messages: List[Message], *, max_chars: int = MAX_TRAJECTORY_CHARS) -> str:
    """Render messages for the judge, keeping head and tail when over budget."""
    from openviking.session.working_memory import format_message_for_wm

    lines = [format_message_for_wm(message) for message in messages]
    if sum(len(line) + 1 for line in lines) <= max_chars:
        return "\n".join(lines)
    # ponytail: head+tail truncation; a memory used only mid-session may be misjudged.
    # Score per extraction batch if long sessions need it.
    half = max_chars // 2
    head: List[str] = []
    size = 0
    for line in lines:
        if size + len(line) > half:
            break
        head.append(line)
        size += len(line) + 1
    tail: List[str] = []
    size = 0
    for line in reversed(lines[len(head) :]):
        if size + len(line) > half:
            break
        tail.append(line)
        size += len(line) + 1
    tail.reverse()
    omitted = len(lines) - len(head) - len(tail)
    return "\n".join([*head, f"...({omitted} messages omitted)...", *tail])


def parse_verdicts(raw: str, candidates: Mapping[str, Any]) -> List[Verdict]:
    """Keep well-formed verdicts for known URIs; the first verdict per URI wins."""
    try:
        items = json.loads(extract_json_content(raw or ""))
    except (TypeError, ValueError):
        logger.warning("Usage feedback response is not JSON; skipping")
        return []
    if not isinstance(items, list):
        return []
    verdicts: Dict[str, Verdict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        uri = item.get("uri")
        score = item.get("score")
        if uri not in candidates or uri in verdicts:
            continue
        if isinstance(score, bool) or score not in _SCORES:
            continue
        reason = str(item.get("reason") or "")[:MAX_REASON_CHARS]
        verdicts[uri] = Verdict(uri=uri, score=int(score), reason=reason)
    return list(verdicts.values())


async def score_usage(
    vlm: Any, messages: List[Message], memories: Mapping[str, str]
) -> List[Verdict]:
    """One LLM call judging every memory in ``memories`` (uri -> file content)."""
    if not memories:
        return []
    from openviking.prompts import render_prompt

    rendered_memories = "\n\n".join(
        f'<memory uri="{uri}">\n'
        f"{MemoryFileUtils.truncate_content(MemoryFileUtils.read(raw, uri=uri).plain_content(), MAX_MEMORY_CHARS)}\n"
        "</memory>"
        for uri, raw in memories.items()
    )
    prompt = render_prompt(
        "memory.usage_feedback",
        {"messages": render_trajectory(messages), "memories": rendered_memories},
    )
    return parse_verdicts(await vlm.get_completion_async(prompt), memories)


def apply_verdict(
    memory_file: MemoryFile, verdict: Verdict, archive_key: str, now: datetime
) -> bool:
    """Accumulate one verdict; False when this archive was already counted."""
    current = memory_file.extra_fields.get(USAGE_FEEDBACK_FIELD)
    stats = dict(current) if isinstance(current, dict) else {}
    if stats.get("last_archive") == archive_key:
        return False
    counter = {1: "helpful", -1: "harmful", 0: "unused"}[verdict.score]
    for key in ("value", "helpful", "harmful", "unused"):
        stats[key] = int(stats.get(key) or 0)
    stats["value"] += verdict.score
    stats[counter] += 1
    stats["last_archive"] = archive_key
    stats["last_reason"] = verdict.reason
    stats["updated_at"] = now.isoformat()
    memory_file.extra_fields[USAGE_FEEDBACK_FIELD] = stats
    return True


async def write_back(
    viking_fs: Any,
    ctx: Any,
    verdicts: List[Verdict],
    archive_key: str,
    now: datetime,
) -> Dict[str, int]:
    """Persist verdicts file by file; one failed file does not stop the rest.

    ponytail: plain read-modify-write, so two commits scoring the same file at
    once can lose one count; add a version check if exact counts matter.
    """
    written: Dict[str, int] = {}
    for verdict in verdicts:
        try:
            raw = await viking_fs.read_file(verdict.uri, ctx=ctx)
            if not raw:
                continue
            memory_file = MemoryFileUtils.read(raw, uri=verdict.uri)
            if not apply_verdict(memory_file, verdict, archive_key, now):
                continue
            await viking_fs.write_file(verdict.uri, MemoryFileUtils.write(memory_file), ctx=ctx)
            written[verdict.uri] = verdict.score
        except Exception as exc:
            logger.warning("Usage feedback write failed for %s: %s", verdict.uri, exc)
    return written


async def read_candidates(viking_fs: Any, ctx: Any, uris: List[str]) -> Dict[str, str]:
    """Raw content of each candidate that still exists."""
    memories: Dict[str, str] = {}
    for uri in uris:
        try:
            raw = await viking_fs.read_file(uri, ctx=ctx)
        except Exception as exc:
            logger.debug("Usage feedback candidate %s unreadable: %s", uri, exc)
            continue
        if raw:
            memories[uri] = raw
    return memories


def peer_allowed(uri: str, *, allow_self: bool, allowed_peer_ids: Optional[List[str]]) -> bool:
    """Mirror the extraction scope: own memories need self, peer memories need the peer."""
    parts = uri.split("/")
    if "peers" in parts:
        index = parts.index("peers")
        peer_id = parts[index + 1] if index + 1 < len(parts) else ""
        return bool(allowed_peer_ids) and peer_id in allowed_peer_ids
    return allow_self
