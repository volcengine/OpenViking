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


def resolve_manual_conflicts(previous: str, edited: str) -> tuple[str, list[str] | None]:
    """Compare previous/edited bodies; return text with stored records and conflict tags.

    No records returns tags=None; invalid records raise InvalidArgumentError.
    """
    matches = list(_CONFLICT_BLOCK.finditer(previous))
    if not matches:
        return edited, None
    previous_body = _CONFLICT_BLOCK.sub("", previous)
    edited_body = _CONFLICT_BLOCK.sub("", edited)
    blocks = []
    unfixed = 0
    now = int(time.time())
    for match in matches:
        try:
            record = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            raise InvalidArgumentError("CONFLICTS_CONTENT must contain a JSON object") from exc
        if not isinstance(record, dict):
            raise InvalidArgumentError("CONFLICTS_CONTENT must contain a JSON object")
        block = match.group(0)
        if record.get("state") == "unfixed":
            anchor = record.get("match_text")
            if not isinstance(anchor, str) or not anchor:
                raise InvalidArgumentError("unfixed conflict match_text must be non-empty text")
            # shortcut: missing anchors count as fixed; map spans if ambiguity matters.
            if anchor in previous_body and anchor not in edited_body:
                record.update(
                    state="fixed",
                    fix_plan=_MANUAL_RESOLUTION,
                    fix_time=now,
                    resolution_basis=_MANUAL_RESOLUTION,
                )
                block = (
                    "<!-- CONFLICTS_CONTENT\n"
                    + json.dumps(record, ensure_ascii=False, indent=2)
                    + "\n-->"
                )
            else:
                unfixed += 1
        blocks.append(block)

    stored_blocks = iter(blocks)
    result = _CONFLICT_BLOCK.sub(lambda _: next(stored_blocks, ""), edited)
    remaining = list(stored_blocks)
    if remaining:
        result = result.rstrip() + "\n\n" + "\n\n".join(remaining) + "\n"
    return result, [f"has_conflict={'true' if unfixed else 'false'}", f"num={unfixed}"]
