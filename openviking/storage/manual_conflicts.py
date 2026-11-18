# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Manual conflict resolution for Markdown resources."""

from __future__ import annotations

import json
import re
import time
from difflib import SequenceMatcher

from openviking_cli.exceptions import InvalidArgumentError

_CONFLICT_BLOCK = re.compile(r"<!--\s*CONFLICTS_CONTENT\b(.*?)-->", re.DOTALL)
_MANUAL_RESOLUTION = "用户手动解决内容冲突，修正后的内容如正文所示"
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


def _find_approximate_anchor(
    previous: str,
    edited: str,
    anchor: str,
    changes: list[tuple[str, int, int, int, int]],
) -> str:
    """Return text in nonblank edited using line changes, then the nearest paragraph.

    Missing old anchors use the first paragraph; matches may be ambiguous or unrelated.
    """
    position = previous.find(anchor) if anchor else -1
    edited_lines = edited.split("\n")
    mapped_start = mapped_end = 0
    if position >= 0:
        start_line = previous.count("\n", 0, position)
        end_line = previous.count("\n", 0, position + len(anchor) - 1) + 1
        spans = []
        for operation, old_start, old_end, new_start, new_end in changes:
            if old_start < end_line and start_line < old_end:
                if operation == "equal":
                    new_end = new_start + min(end_line, old_end) - old_start
                    new_start += max(start_line, old_start) - old_start
                spans.append((new_start, new_end))
        if spans:
            mapped_start, mapped_end = spans[0][0], spans[-1][1]
            candidate = "\n".join(edited_lines[mapped_start:mapped_end]).strip()
            if candidate:
                return candidate

    offset = sum(len(line) + 1 for line in edited_lines[:mapped_start])
    paragraphs = list(re.finditer(r"\S.*?(?=\n\s*\n|\Z)", edited, re.DOTALL))
    nearest = min(
        paragraphs,
        key=lambda paragraph: max(paragraph.start() - offset, offset - paragraph.end(), 0),
    )
    return nearest.group().strip()


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


def conflict_tags_from_content(content: str) -> list[str]:
    """Return Markdown conflict tags (zero if absent); invalid metadata raises InvalidArgumentError."""
    if re.search(r"<!--\s*CONFLICTS_CONTENT\b", _CONFLICT_BLOCK.sub("", content)):
        raise InvalidArgumentError("CONFLICTS_CONTENT comment must be closed")
    unfixed = 0
    for match in _CONFLICT_BLOCK.finditer(content):
        for record in _parse_conflict_records(match.group(1)):
            if record.get("state") not in ("fixed", "unfixed"):
                raise InvalidArgumentError("conflict state must be fixed or unfixed")
            unfixed += record["state"] == "unfixed"
    return [f"has_conflict={'true' if unfixed else 'false'}", f"conflict_num={unfixed}"]


def resolve_manual_conflicts(
    previous: str, edited: str, user_id: str
) -> tuple[str, list[str] | None]:
    """Return edited text and conflict tags, attributing newly fixed records to user_id.

    Blank bodies clear history and tags.
    No old comments yield tags=None unless the body is blank. Invalid records raise
    InvalidArgumentError.
    """
    matches = list(_CONFLICT_BLOCK.finditer(previous))
    records_by_block = [_parse_conflict_records(match.group(1)) for match in matches]
    edited_body = _CONFLICT_BLOCK.sub("", edited)
    if not edited_body.strip():
        return edited_body, ["has_conflict=false", "conflict_num=0"]
    if not matches:
        return edited, None
    previous_body = _CONFLICT_BLOCK.sub("", previous)
    old_anchors = {
        record["match_text"]
        for records in records_by_block
        for record in records
        if isinstance(record.get("match_text"), str) and record["match_text"]
    }
    blocks = []
    unfixed = 0
    now = int(time.time())
    changes = None
    for match, records in zip(matches, records_by_block, strict=True):
        block = match.group(0)
        changed = False
        for record in records:
            anchor = record.get("match_text")
            if record.get("state") == "unfixed":
                # shortcut: disappearance counts as resolution; semantic proof needs validation.
                if anchor in previous_body and anchor not in edited_body:
                    record.update(
                        state="fixed",
                        fix_plan=_MANUAL_RESOLUTION,
                        fix_time=now,
                        resolution_basis="人工修复",
                        resolved_by=user_id,
                    )
                    changed = True
                else:
                    unfixed += 1
            if not isinstance(anchor, str) or not anchor or anchor not in edited_body:
                rewritten = (
                    _find_rewritten_anchor(previous_body, edited_body, anchor, old_anchors)
                    if isinstance(anchor, str) and anchor
                    else None
                )
                if rewritten is None:
                    if changes is None:
                        changes = SequenceMatcher(
                            None,
                            previous_body.split("\n"),
                            edited_body.split("\n"),
                            autojunk=False,
                        ).get_opcodes()
                    rewritten = _find_approximate_anchor(
                        previous_body,
                        edited_body,
                        anchor if isinstance(anchor, str) else "",
                        changes,
                    )
                record["match_text"] = rewritten
                changed = True
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
