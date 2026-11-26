# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Index selected archive passages and return excerpts with their source URI."""

import hashlib
import json
from typing import TYPE_CHECKING, Any

from openviking.core.namespace import canonical_user_root
from openviking.models.embedder.base import embed_compat
from openviking.pyagfs.exceptions import AGFSClientError
from openviking.server.identity import RequestContext
from openviking.session.archive_store import ArchiveStore
from openviking.storage.archive_source import archive_source_hit, is_archive_source_uri
from openviking_cli.exceptions import (
    InvalidArgumentError,
    NotFoundError,
    OpenVikingError,
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
ARCHIVE_SOURCE_CHATLOG_PROOFS = "archive_source_chatlog_proofs"


async def uncovered_archive_source_excerpts(
    fs: "VikingFS", metadata: dict[str, Any], ctx: RequestContext
) -> list[str]:
    """Recheck coverage at enqueue time, after all extraction batches have written."""
    candidates = metadata.get(ARCHIVE_SOURCE_CANDIDATES)
    if not isinstance(candidates, list):
        return []
    proofs = metadata.get(ARCHIVE_SOURCE_CHATLOG_PROOFS)
    if not isinstance(proofs, dict):
        proofs = {}
    uncovered = []
    verified: dict[str, str] = {}
    event_root = f"{canonical_user_root(ctx)}/memories/events/"
    for excerpt in candidates:
        if not isinstance(excerpt, str):
            continue
        proof = proofs.get(excerpt)
        if isinstance(proof, dict):
            uri, digest = proof.get("uri"), proof.get("sha256")
            if isinstance(uri, str) and uri.startswith(event_root) and isinstance(digest, str):
                if uri not in verified:
                    try:
                        raw = await fs.read_file(uri, ctx=ctx)
                        verified[uri] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                    except (AGFSClientError, OSError, ValueError, OpenVikingError):
                        verified[uri] = ""
                if digest and verified[uri] == digest:
                    continue
        uncovered.append(excerpt)
    return uncovered


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
    candidates = meta.get(ARCHIVE_SOURCE_CANDIDATES)
    if not recorded_hash or not isinstance(candidates, list):
        return set()
    raw = await fs.read_file(source_uri, ctx=ctx)
    if hashlib.sha256(raw.encode("utf-8")).hexdigest() != recorded_hash:
        return set()
    return {excerpt for excerpt in candidates if isinstance(excerpt, str)}


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
                selected_by_uri[source_uri] = set()
                selected_by_uri[source_uri] = await _selected_excerpts(fs, source_uri, ctx)
            if excerpt not in selected_by_uri[source_uri]:
                continue

            read_ctx = fs.authorize_read(ctx, [source_uri])
            sanitizer = fs._privacy_sanitizer
            if sanitizer is not None and read_ctx.data_access is None:
                settings = await sanitizer.settings_for_uri(source_uri, read_ctx)
                if settings is not None:
                    # Treat the excerpt as a message value, even when it contains JSON.
                    sanitized = await sanitizer.sanitize(
                        json.dumps(excerpt, ensure_ascii=False),
                        ctx=read_ctx,
                        uri=source_uri,
                        settings=settings,
                    )
                    excerpt = json.loads(sanitized.content)
        except (
            AGFSClientError,
            OSError,
            InvalidArgumentError,
            NotFoundError,
            PermissionDeniedError,
            ResourceExhaustedError,
        ):
            continue
        sources.append({**hit, "abstract": excerpt})
    return sources
