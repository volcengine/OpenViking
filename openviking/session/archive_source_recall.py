# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Index selected archive passages and return excerpts with their source URI."""

import hashlib
from typing import TYPE_CHECKING, Any

from openviking.models.embedder.base import embed_compat
from openviking.server.identity import RequestContext
from openviking.session.archive_store import ArchiveStore
from openviking.storage.archive_source import archive_source_hit, is_archive_source_uri
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
    excerpts: list[str],
    ctx: RequestContext,
) -> None:
    store = fs._get_vector_store()
    if not excerpts or store is None:
        return
    for excerpt in excerpts:
        if not isinstance(excerpt, str):
            continue
        try:
            await store.enqueue_archive_source(
                archive_uri=f"{archive_uri}/messages.jsonl",
                excerpt=excerpt,
                ctx=ctx,
            )
        except Exception as exc:
            logger.warning("Could not enqueue archive source error_type=%s", type(exc).__name__)


def _require_enabled() -> None:
    if not get_openviking_config().memory.archive_source_selection_enabled:
        raise NotFoundError("archive source recall", "feature")


async def _selected_excerpts(fs: "VikingFS", source_uri: str, ctx: RequestContext) -> set[str]:
    if not is_archive_source_uri(source_uri, ctx):
        return set()
    archive_uri = source_uri.rsplit("/", 1)[0]
    archive = ArchiveStore(fs, ctx, archive_uri.rsplit("/", 2)[0])
    meta = await archive.read_meta(archive_uri)
    phase1 = meta.get("phase1")
    recorded_hash = phase1.get("messages_sha256") if isinstance(phase1, dict) else None
    if not recorded_hash:
        return set()
    raw = await fs.read_file(source_uri, ctx=ctx)
    if hashlib.sha256(raw.encode("utf-8")).hexdigest() != recorded_hash:
        return set()
    return {
        excerpt
        for excerpt in meta.get(ARCHIVE_SOURCE_CANDIDATES) or []
        if isinstance(excerpt, str)
    }


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
    visible = await visible_archive_source_hits(fs, hits, ctx)
    return {"sources": [archive_source_hit(hit) for hit in visible]}


async def visible_archive_source_hits(
    fs: "VikingFS", hits: list[dict[str, Any]], ctx: RequestContext
) -> list[dict[str, Any]]:
    """Validate the archived bytes and apply read-time visibility to recalled excerpts."""
    selected_by_uri: dict[str, set[str]] = {}
    sources = []
    for hit in hits:
        source_uri, excerpt = hit.get("uri"), hit.get("abstract")
        if not isinstance(source_uri, str) or not isinstance(excerpt, str):
            continue
        try:
            if source_uri not in selected_by_uri:
                selected_by_uri[source_uri] = await _selected_excerpts(fs, source_uri, ctx)
            if excerpt not in selected_by_uri[source_uri]:
                continue

            read_ctx = fs.authorize_read(ctx, [source_uri])
            sanitizer = fs._privacy_sanitizer
            if sanitizer is not None and read_ctx.data_access is None:
                settings = await sanitizer.settings_for_uri(source_uri, read_ctx)
                if settings is not None:
                    excerpt = (
                        await sanitizer.sanitize(
                            excerpt, ctx=read_ctx, uri=source_uri, settings=settings
                        )
                    ).content
        except (InvalidArgumentError, NotFoundError, PermissionDeniedError, ResourceExhaustedError):
            continue
        sources.append({**hit, "abstract": excerpt})
    return sources
