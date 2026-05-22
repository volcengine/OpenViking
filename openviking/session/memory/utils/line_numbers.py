# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import re
from typing import Optional

_LINE_NUMBER_PREFIX_RE = re.compile(r"^(\d+)\t")
_LINE_NUMBER_PREFIX_WITH_LEADING_SPACE_RE = re.compile(r"^\s*(\d+)\t")
_LINE_SPLIT_RE = re.compile(r"\r?\n")


def split_content_lines(content: str) -> list[str]:
    if content == "":
        return []
    return _LINE_SPLIT_RE.split(content)


def add_line_numbers(content: str, start_line: int = 1) -> str:
    if not content:
        return ""
    return "\n".join(
        f"{index + start_line}\t{line}" for index, line in enumerate(split_content_lines(content))
    )


def slice_content_lines(content: str, offset: int = 0, limit: int = -1) -> str:
    lines = split_content_lines(content)
    if offset >= len(lines):
        return ""
    end = None if limit < 0 else offset + limit
    return "\n".join(lines[offset:end])


def line_count(content: str) -> int:
    return len(split_content_lines(content))


def extract_start_line_number(content: str) -> Optional[int]:
    first_line = content.split("\n", 1)[0]
    match = _LINE_NUMBER_PREFIX_WITH_LEADING_SPACE_RE.match(first_line)
    if match is None:
        return None
    return int(match.group(1))


def strip_line_numbers(content: str, aggressive: bool = False) -> str:
    pattern = _LINE_NUMBER_PREFIX_WITH_LEADING_SPACE_RE if aggressive else _LINE_NUMBER_PREFIX_RE
    return "\n".join(pattern.sub("", line) for line in split_content_lines(content))


def every_line_has_line_numbers(content: str) -> bool:
    lines = split_content_lines(content)
    if not lines:
        return False
    return all(_LINE_NUMBER_PREFIX_RE.match(line) for line in lines)
