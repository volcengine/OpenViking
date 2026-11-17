# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Searchable excerpts that point to the original committed messages."""

import re
from collections.abc import Sequence

from openviking.core.namespace import is_session_uri, uri_parts
from openviking.message import Message, TextPart
from openviking.server.identity import RequestContext
from openviking.storage.viking_fs import VikingFS
from openviking.utils.embedding_utils import vectorize_file

ARCHIVE_SOURCE_MEMORY_TYPE = "archive_sources"
_EXCERPT_CHARS = 2048
_EXCERPT_SAMPLES = 8
_ARCHIVE_ID = re.compile(r"archive_[0-9]+")


def _excerpt(text: str) -> str:
    if len(text) <= _EXCERPT_CHARS:
        return text
    separator = "\n[…]\n"
    width = (_EXCERPT_CHARS - len(separator) * (_EXCERPT_SAMPLES - 1)) // _EXCERPT_SAMPLES
    last_start = len(text) - width
    offsets = (i * last_start // (_EXCERPT_SAMPLES - 1) for i in range(_EXCERPT_SAMPLES))
    return separator.join(text[start : start + width] for start in offsets)


def archive_source_index_uri(session_path: str) -> str | None:
    """Map a session or archived transcript to its associated index subtree."""
    if not is_session_uri(session_path):
        return None
    parts = uri_parts(session_path)
    _, owner, _, *relative = parts
    root = f"viking://user/{owner}/memories/{ARCHIVE_SOURCE_MEMORY_TYPE}"
    if not relative:
        return root
    session_id, *suffix = relative
    root = f"{root}/{session_id}"
    if not suffix or suffix == ["history"]:
        return root
    if (
        len(suffix) in (2, 3)
        and suffix[0] == "history"
        and _ARCHIVE_ID.fullmatch(suffix[1])
        and (len(suffix) == 2 or suffix[2] == "messages.jsonl")
    ):
        return f"{root}/{suffix[1]}"
    return None


async def index_archive_sources(
    fs: VikingFS, archive_uri: str, messages: Sequence[Message], ctx: RequestContext
) -> None:
    """Write stable per-message sources and enqueue their normal memory vectors."""
    directory = archive_source_index_uri(archive_uri)
    assert directory is not None, "Expected a canonical session archive URI"
    transcript = f"{archive_uri}/messages.jsonl"
    for line, message in enumerate(messages, start=1):
        if message.role not in ("user", "assistant") or message.message_kind in (
            "checkpoint",
            "tool_transport",
        ):
            continue
        text = "\n".join(part.text for part in message.parts if isinstance(part, TextPart)).strip()
        if not text:
            continue
        name = f"message_{line:06d}.md"
        uri = f"{directory}/{name}"
        content = (
            f"## Archived {message.role} message\n\n"
            f"- Source: `{transcript}`\n"
            f"- Message ID: `{message.id}`\n"
            f"- Line: {line}\n\n"
            f"{_excerpt(text)}\n"
        )
        await fs.write_file(uri, content, ctx=ctx)
        # Embed the persisted text so write-time privacy filtering also applies
        # to the excerpt returned by search and passed to the embedder.
        persisted = await fs.read_file(uri, ctx=ctx)
        if not await vectorize_file(
            file_path=uri,
            summary_dict={"name": name, "summary": persisted},
            parent_uri=directory,
            context_type="memory",
            use_summary=True,
            ctx=ctx,
            action="upsert",
        ):
            raise RuntimeError(f"Failed to enqueue archive source index: {uri}")


async def remove_archive_sources(fs: VikingFS, session_path: str, ctx: RequestContext) -> None:
    directory = archive_source_index_uri(session_path)
    if directory is not None and await fs.exists(directory, ctx=ctx):
        await fs.rm(directory, recursive=True, ctx=ctx)
