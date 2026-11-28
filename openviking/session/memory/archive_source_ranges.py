# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Range hints for selecting passages from archived messages."""

import re
from dataclasses import dataclass

from openviking.message import Message
from openviking.message.part import TextPart

ARCHIVE_SOURCE_RANGES_FIELD = "archive_source_ranges"
MAX_ARCHIVE_SOURCE_SPANS = 16
ARCHIVE_SOURCE_RANGE_INSTRUCTION = (
    "Optional zero-based indexes of messages or chunks in the supplied Conversation History. "
    'Use strings such as "3" or "7-9". Select passages worth looking up verbatim later because '
    "they contain precise details, constraints, decisions, or work results. Use at most 16 "
    "message or chunk positions. Omit routine conversation. Only select text from user or "
    "assistant messages. Keep candidates for useful details even if a proposed Event "
    "references them. The service removes candidates fully covered by a successfully "
    "written Events ChatLog. "
    "Use an empty list when there are no useful sources. Do not quote or summarize the text."
)
_INDEX_RANGE = re.compile(r"([0-9]{1,9})(?:-([0-9]{1,9}))?")


@dataclass(frozen=True, slots=True)
class ArchiveSourceExcerpt:
    """A selected original excerpt and its message ID for archive membership checks."""

    message_id: str
    excerpt: str
    chatlog_uri: str = ""
    chatlog_sha256: str = ""


def plain_archive_text(message: Message) -> str:
    """Only plain archived user and assistant text is supported in v1."""
    if (
        message.interrupted
        or message.role not in {"user", "assistant"}
        or not all(isinstance(part, TextPart) for part in message.parts)
    ):
        return ""
    return "".join(part.text for part in message.parts)


def selected_archive_source_indexes(ranges: list[str], message_count: int) -> list[int]:
    """Validate prompt-local range hints and preserve the model's selection order."""
    indexes: dict[int, None] = {}
    for value in ranges[:MAX_ARCHIVE_SOURCE_SPANS]:
        match = _INDEX_RANGE.fullmatch(value.strip()) if isinstance(value, str) else None
        if not match:
            continue
        start = int(match[1])
        end = int(match[2]) if match[2] is not None else start
        if not start <= end < message_count:
            continue
        for index in range(start, end + 1):
            indexes[index] = None
            if len(indexes) == MAX_ARCHIVE_SOURCE_SPANS:
                return list(indexes)
    return list(indexes)
