# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Index selected archive passages and resolve them for agent tools."""

from typing import TYPE_CHECKING, Any

from openviking.message import Message
from openviking.models.embedder.base import embed_compat
from openviking.server.identity import RequestContext
from openviking.session.archive_store import ArchiveStore
from openviking.session.memory.archive_source_ranges import plain_archive_text
from openviking.storage.archive_source import MAX_ARCHIVE_SOURCE_CHARS, is_archive_source_uri
from openviking_cli.exceptions import (
    InvalidArgumentError,
    NotFoundError,
    PermissionDeniedError,
    ResourceExhaustedError,
)
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

if TYPE_CHECKING:
    from openviking.service.fs_service import FSService
    from openviking.storage.viking_fs import VikingFS

logger = get_logger(__name__)
ARCHIVE_SOURCE_CANDIDATES = "archive_source_candidates"


async def enqueue_archive_sources(
    fs: "VikingFS",
    archive_uri: str,
    messages: list[Message],
    candidates: list[dict[str, Any]],
    ctx: RequestContext,
) -> None:
    store = fs._get_vector_store()
    if not candidates or store is None:
        return
    messages_by_id = {message.id: message for message in messages}
    for candidate in candidates:
        message = messages_by_id.get(candidate["message_id"])
        if message is None:
            continue
        start, end = candidate["start_char"], candidate["end_char"]
        text = plain_archive_text(message)
        if not 0 <= start < end <= len(text):
            continue
        end = min(end, start + MAX_ARCHIVE_SOURCE_CHARS)
        excerpt = text[start:end]
        if not excerpt.strip():
            continue
        try:
            await store.enqueue_archive_source(
                archive_uri=f"{archive_uri}/messages.jsonl",
                message_id=message.id,
                start=start,
                end=end,
                excerpt=excerpt,
                ctx=ctx,
            )
        except Exception as exc:
            logger.warning("Could not enqueue archive source error_type=%s", type(exc).__name__)


def _require_enabled() -> None:
    if not get_openviking_config().memory.archive_source_selection_enabled:
        raise NotFoundError("archive source recall", "feature")


async def _open_source_archive(
    fs: "VikingFS", source_ref: dict[str, Any], ctx: RequestContext
) -> tuple[ArchiveStore, str]:
    uri = source_ref["archive_uri"]
    if not is_archive_source_uri(uri, ctx):
        raise InvalidArgumentError("Expected a source from the current user's archived messages")
    await fs._ensure_access(uri, ctx)
    if not await fs.exists(uri, ctx=ctx):
        raise NotFoundError("archive source message", "archive source")

    archive_uri = uri.rsplit("/", 1)[0]
    archive = ArchiveStore(fs, ctx, archive_uri.rsplit("/", 2)[0])
    candidates = (await archive.read_meta(archive_uri)).get(ARCHIVE_SOURCE_CANDIDATES) or []
    start, end = source_ref["start_char"], source_ref["end_char"]
    for candidate in candidates:
        if (
            candidate["message_id"] == source_ref["message_id"]
            and start == candidate["start_char"]
            and end == min(candidate["end_char"], start + MAX_ARCHIVE_SOURCE_CHARS)
            and start < end
        ):
            return archive, archive_uri
    raise NotFoundError("archive source reference", "archive source")


async def search_archive_sources(
    fs_service: "FSService", query: str, limit: int, ctx: RequestContext
) -> dict[str, Any]:
    _require_enabled()
    if not query.strip():
        raise InvalidArgumentError("Archive source search requires a query")
    try:
        query.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise InvalidArgumentError("Archive source query must contain valid Unicode") from exc
    fs = fs_service._ensure_initialized()
    store = fs._get_vector_store()
    if store is None:
        return {"sources": []}
    backend = await store.get_account_backend(ctx.account_id)
    if not await backend.collection_exists():
        return {"sources": []}
    vector = await embed_compat(fs._get_embedder(ctx), query, is_query=True)
    hits = await store.search_archive_sources(
        query_vector=vector.dense_vector,
        sparse_query_vector=vector.sparse_vector,
        limit=limit,
        ctx=ctx,
    )
    sources = []
    for hit in hits:
        source_ref = {
            "archive_uri": hit["archive_uri"],
            "message_id": hit["message_id"],
            "start_char": hit["start"],
            "end_char": hit["end"],
        }
        try:
            await _open_source_archive(fs, source_ref, ctx)
            excerpt = await fs_service.make_visible(hit["archive_uri"], hit["excerpt"], ctx)
        except (InvalidArgumentError, NotFoundError, PermissionDeniedError, ResourceExhaustedError):
            continue
        sources.append({"excerpt": excerpt, "source_ref": source_ref, "score": hit["score"]})
    return {"sources": sources}


async def read_archive_source(
    fs_service: "FSService", source_ref: dict[str, Any], ctx: RequestContext
) -> dict[str, str]:
    _require_enabled()
    archive, archive_uri = await _open_source_archive(
        fs_service._ensure_initialized(), source_ref, ctx
    )
    for message in await archive.read_messages(archive_uri):
        if message.id != source_ref["message_id"]:
            continue
        text = plain_archive_text(message)
        if source_ref["end_char"] <= len(text):
            start = max(0, source_ref["start_char"] - MAX_ARCHIVE_SOURCE_CHARS)
            end = source_ref["end_char"] + MAX_ARCHIVE_SOURCE_CHARS
            return {
                "content": await fs_service.make_visible(
                    source_ref["archive_uri"], text[start:end], ctx
                )
            }
    raise NotFoundError("archive source message", "archive source")
