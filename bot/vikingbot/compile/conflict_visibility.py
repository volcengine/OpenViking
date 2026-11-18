# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Non-destructive model-input projection of reserved Wiki comments."""

import re

from markdown_it import MarkdownIt
from markdown_it.rules_inline import html_inline

_CONFLICT = re.compile(r"<!--\s*CONFLICTS_CONTENT\b.*?(?:-->|\Z)", re.S)


def without_conflict_comments(text: str) -> str:
    """Exclude real HTML conflict blocks, preserving code and ordinary comments.

    Parse only when the reserved marker occurs. The source bytes, source hashes
    and authorizations are never changed by this summary-input projection.
    """
    if "CONFLICTS_CONTENT" not in text:
        return text
    # This is only a model-facing view; normalize as CommonMark does.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    offsets = [0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    spans: list[tuple[int, int]] = []
    parser = MarkdownIt("commonmark")

    def capture(state, silent):
        start = state.pos
        accepted = html_inline(state, silent)
        if accepted and not silent and _CONFLICT.fullmatch(state.src[start : state.pos]):
            state.env.setdefault("hidden", []).append((start, state.pos))
        return accepted

    parser.inline.ruler.at("html_inline", capture)
    for token in parser.parse(text):
        if token.map is None:
            continue
        start, end = (offsets[i] for i in token.map)
        if token.type == "html_block":
            spans.extend(
                (start + m.start(), start + m.end()) for m in _CONFLICT.finditer(text[start:end])
            )
        elif token.type == "inline" and any(c.type == "html_inline" for c in token.children or ()):
            env: dict[str, list[tuple[int, int]]] = {}
            parser.inline.parse(token.content, parser, env, [])
            # Container prefixes (blockquote/list indentation) are absent from
            # token.content. Align its lines separately instead of assuming one
            # contiguous source substring, and retain the original source spans.
            positions = []
            cursor = start
            for line in token.content.splitlines(keepends=True):
                body = line.removesuffix("\n")
                at = text.find(body, cursor, end)
                leading = 0
                if at < 0:
                    trimmed = body.lstrip(" \t")
                    leading = len(body) - len(trimmed)
                    at = text.find(trimmed, cursor, end)
                if at < 0:
                    break
                positions.extend([at] * leading)
                positions.extend(range(at, at + len(body) - leading))
                cursor = at + len(body) - leading
                if line.endswith("\n"):
                    newline = text.find("\n", cursor, end)
                    if newline < 0:
                        break
                    positions.append(newline)
                    cursor = newline + 1
            if len(positions) == len(token.content):
                spans.extend((positions[a], positions[b - 1] + 1) for a, b in env.get("hidden", ()))
    for start, end in sorted(set(spans), reverse=True):
        text = text[:start] + text[end:]
    return text
