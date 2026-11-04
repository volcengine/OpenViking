# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Manual conflict resolution for Markdown resources."""

from __future__ import annotations

import json
import re
import time

from openviking_cli.exceptions import InvalidArgumentError

_CONFLICT_BLOCK = re.compile(r"<!--\s*CONFLICTS_CONTENT\b(.*?)-->", re.DOTALL)
_MANUAL_RESOLUTION = (
    "用户手动解决内容冲突，修正后的内容如正文所示，后续更新的时候不能修改这部分内容"
)
# Fixed contexts prevent matching neighboring edits.
_ANCHOR_CONTEXT_CHARS = 10


def _find_rewritten_anchor(
    previous: str, edited: str, anchor: str, old_anchors: set[str]
) -> str | None:
    """Return anchor's unique rewrite; changed contexts or overlapping old_anchors yield None."""
    position = previous.find(anchor)
    end = position + len(anchor)
    if position < 0 or previous.find(anchor, position + 1) >= 0:
        return None

    context_start = max(0, position - _ANCHOR_CONTEXT_CHARS)
    context_end = min(len(previous), end + _ANCHOR_CONTEXT_CHARS)
    left, right = previous[context_start:position], previous[end:context_end]
    if not left.strip() and not right.strip():
        return None
    # shortcut: changed contexts retain anchors; broader edits require explicit spans.
    for context in (left, right):
        if context and (
            previous.find(context) != previous.rfind(context)
            or context not in edited
            or edited.find(context) != edited.rfind(context)
        ):
            return None
    new_start = edited.find(left) + len(left) if left else 0
    new_end = edited.find(right) if right else len(edited)
    if (
        (context_start == 0 and edited[:new_start] != left)
        or (context_end == len(previous) and edited[new_end:] != right)
        or new_end <= new_start
    ):
        return None
    candidate = edited[new_start:new_end].strip()
    if not candidate or edited.find(candidate) != edited.rfind(candidate):
        return None
    for other in old_anchors:
        if other != anchor and (
            other in candidate
            # Limit the search to occurrences overlapping the old anchor.
            or previous.find(other, max(0, position - len(other) + 1), end + len(other) - 1) >= 0
        ):
            return None
    return candidate


def _parse_conflict_records(payload: str) -> list[dict]:
    """Parse comma-separated objects (possibly empty); reject invalid records."""
    try:
        records = json.loads("[" + payload + "]")
    except json.JSONDecodeError as exc:
        raise InvalidArgumentError(
            "CONFLICTS_CONTENT must contain comma-separated JSON objects"
        ) from exc
    for record in records:
        if not isinstance(record, dict):
            raise InvalidArgumentError(
                "CONFLICTS_CONTENT must contain comma-separated JSON objects"
            )
        if record.get("state") == "unfixed" and (
            not isinstance(record.get("match_text"), str) or not record["match_text"]
        ):
            raise InvalidArgumentError("unfixed conflict match_text must be non-empty text")
    return records


def resolve_manual_conflicts(previous: str, edited: str) -> tuple[str, list[str] | None]:
    """Return edited text with conflict history and tags; uncertain anchors stay unchanged.

    Missing comments yield tags=None; empty comments count as zero conflicts.
    Invalid records raise InvalidArgumentError.
    """
    matches = list(_CONFLICT_BLOCK.finditer(previous))
    if not matches:
        return edited, None
    previous_body = _CONFLICT_BLOCK.sub("", previous)
    edited_body = _CONFLICT_BLOCK.sub("", edited)
    records_by_block = [_parse_conflict_records(match.group(1)) for match in matches]
    old_anchors = {
        record["match_text"]
        for records in records_by_block
        for record in records
        if isinstance(record.get("match_text"), str) and record["match_text"]
    }
    blocks = []
    unfixed = 0
    now = int(time.time())
    for match, records in zip(matches, records_by_block, strict=True):
        block = match.group(0)
        changed = False
        for record in records:
            if record.get("state") == "unfixed":
                anchor = record.get("match_text")
                # shortcut: disappearance counts as resolution; semantic proof needs validation.
                if anchor in previous_body and anchor not in edited_body:
                    rewritten = _find_rewritten_anchor(
                        previous_body, edited_body, anchor, old_anchors
                    )
                    record.update(
                        state="fixed",
                        fix_plan=_MANUAL_RESOLUTION,
                        fix_time=now,
                        resolution_basis="人工修复",
                    )
                    if rewritten is not None:
                        record["match_text"] = rewritten
                    changed = True
                else:
                    unfixed += 1
        if changed:
            block = (
                "<!-- CONFLICTS_CONTENT\n"
                + ",\n".join(json.dumps(record, ensure_ascii=False, indent=2) for record in records)
                + "\n-->"
            )
        blocks.append(block)

    stored_blocks = iter(blocks)
    result = _CONFLICT_BLOCK.sub(lambda _: next(stored_blocks, ""), edited)
    remaining = list(stored_blocks)
    if remaining:
        result = result.rstrip() + "\n\n" + "\n\n".join(remaining) + "\n"
    return result, [f"has_conflict={'true' if unfixed else 'false'}", f"conflict_num={unfixed}"]
