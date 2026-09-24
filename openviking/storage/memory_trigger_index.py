# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Retrieval-only multi-view vectors pointing to canonical OV memory files."""

import asyncio
import json

from openviking.concurrency import AsyncSemaphore
from openviking.models.embedder.base import embed_compat
from openviking.server.error_mapping import is_not_found_error
from openviking.session.memory.retrieval_triggers import memory_type_for_uri, source_hash
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.storage.expr import In
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from openviking_cli.exceptions import PermissionDeniedError


class TriggerBackend(VikingVectorIndexBackend):
    def _rewrite_transfer_record(self, record, **kwargs):
        payload = super()._rewrite_transfer_record(record, **kwargs)
        metadata = json.loads(record["description"])
        payload["id"] = MemoryTriggerIndex.record_id(
            kwargs["ctx"].account_id, payload["uri"], metadata["view"]
        )
        return payload


class MemoryTriggerIndex:
    def __init__(self, primary, settings):
        self.primary, self.settings = primary, settings
        self.store = TriggerBackend(
            primary._config.model_copy(
                update={"name": primary.collection_name + "_memory_triggers"}
            )
        )
        self.store.acl_manager = primary.acl_manager
        # Commit and embedding queues run on different worker event loops.
        self.model_slots = AsyncSemaphore(settings.max_concurrent)

    @property
    def recall_enabled(self):
        return self.settings.enabled and self.settings.recall_enabled

    @staticmethod
    def accepts(record):
        return (
            record.get("context_type") == "memory"
            and record.get("level", 2) == 2
            and memory_type_for_uri(record.get("uri", "")) is not None
        )

    @staticmethod
    def record_id(account, uri, view):
        return source_hash(json.dumps([account, uri, view["family"], view["text"]]))[:32]

    async def embeddings(self, state, embedder):
        if not state or not self.settings.enabled:
            return []

        async def one(view):
            async with self.model_slots:
                result = await embed_compat(embedder, view["text"], is_query=False)
            return {
                "view": view,
                "source_sha256": state["source_sha256"],
                "vector": result.dense_vector,
                "sparse_vector": result.sparse_vector or {},
            }

        results = await asyncio.gather(
            *(one(view) for view in state["views"]), return_exceptions=True
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result
            if not result["vector"]:
                raise ValueError("Empty memory trigger embedding")
        return results

    async def sync(self, record, ctx, *, refresh=False, embeddings=None):
        if not self.accepts(record):
            return
        old, _ = await self.store._read_uri_transfer_entries(
            ctx, [record["uri"]], include_full_records=True
        )
        keep = set()
        if refresh:
            for embedded in embeddings or []:
                view = embedded["view"]
                key = self.record_id(ctx.account_id, record["uri"], view)
                if len(embedded["vector"]) != len(record.get("vector") or []):
                    raise ValueError("Memory trigger vector dimension mismatch")
                metadata = {"view": view, "source_sha256": embedded["source_sha256"]}
                payload = {
                    **record,
                    "id": key,
                    "vector": embedded["vector"],
                    "sparse_vector": embedded["sparse_vector"],
                    "description": json.dumps(metadata, ensure_ascii=False),
                    "type": view["family"],
                }
                if not await self.store.upsert(payload, ctx=ctx):
                    raise RuntimeError("Memory trigger index write failed")
                keep.add(key)
        else:
            # Tag/ACL metadata changes do not regenerate cues; changed evidence invalidates them.
            for view in old:
                if view.get("abstract") == record.get("abstract"):
                    payload = {
                        **record,
                        **{
                            k: view[k]
                            for k in ("id", "vector", "sparse_vector", "description", "type")
                            if k in view
                        },
                    }
                    if not await self.store.upsert(payload, ctx=ctx):
                        raise RuntimeError("Memory trigger metadata refresh failed")
                    keep.add(view["id"])
        obsolete = [view["id"] for view in old if view["id"] not in keep]
        if obsolete:
            await self.store.delete(obsolete, ctx=ctx)

    async def search(self, *, ctx, fs, **kwargs):
        if not self.recall_enabled:
            return []
        # Several views can map to one file. Budget after deduplication where possible.
        limit = kwargs.pop("limit", self.settings.candidate_k)
        scope = self.store._build_scope_filter(
            ctx=ctx,
            context_type="memory",
            target_directories=kwargs.get("target_directories"),
            extra_filter=kwargs.get("extra_filter"),
            level=[2],
            acl_enabled=await self.store._acl_enabled(ctx),
        )
        hits = await self.store.search(
            ctx=ctx,
            query_vector=kwargs.get("query_vector"),
            filter=scope,
            limit=limit * self.settings.max_triggers,
            output_fields=["uri", "abstract", "description"],
        )
        if not hits:
            return []
        uris = list(dict.fromkeys(hit["uri"] for hit in hits))
        current = await self.primary.filter_in_tenant(
            ctx=ctx,
            context_type="memory",
            target_directories=kwargs.get("target_directories"),
            extra_filter=self.primary._merge_filters(kwargs.get("extra_filter"), In("uri", uris)),
            level=[2],
            limit=len(uris),
        )
        by_uri, validated, output = {r["uri"]: r for r in current}, {}, {}
        for hit in hits:
            uri = hit["uri"]
            canonical = by_uri.get(uri)
            if canonical is None or canonical.get("abstract") != hit.get("abstract"):
                continue
            try:
                if uri not in validated:
                    raw = await fs.read_file(uri, ctx=ctx)
                    validated[uri] = MemoryFileUtils.read(raw, uri=uri).content
                metadata = json.loads(hit["description"])
                if source_hash(validated[uri]) != metadata["source_sha256"]:
                    continue
                if metadata["view"]["anchor"] not in validated[uri]:
                    continue
                if metadata["view"]["confidence"] < self.settings.min_confidence:
                    continue
            except Exception as exc:
                if is_not_found_error(exc) or isinstance(
                    exc, (PermissionError, PermissionDeniedError, ValueError, KeyError, TypeError)
                ):
                    continue
                raise
            if uri not in output:
                # A trigger's text/metadata never replaces the canonical evidence.
                output[uri] = {**canonical, "_score": hit["_score"]}
        return list(output.values())[:limit]
