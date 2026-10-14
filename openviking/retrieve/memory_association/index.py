# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Derived cue vectors pointing to existing memory files, not new memories.

One row per (memory, cue, source fingerprint) avoids concurrent mutation of
shared linked-memory lists. Independent lookup authorizes live parent memories
and checks their source fingerprints, including after ACL changes.
"""

import asyncio
import hashlib
import json
import math

from openviking.models.embedder.base import embed_compat
from openviking.service.task_tracker_concurrency import KeyedAsyncLockPool
from openviking.storage.expr import And, Eq, In, Or, PathScope
from openviking.storage.record_types import (
    ASSOCIATION_RECORD_TYPES,
    MEMORY_ASSOCIATION_TYPE,
    association_records,
    context_records,
)
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.config.retrieval_config import MemoryAssociationConfig

from ._cue_rules import extract_cues_batch


def source_fingerprint(record: dict) -> str:
    """Do not let a previous version of a memory influence the current result."""
    source = json.dumps([record.get("md5", ""), record.get("abstract", "")], ensure_ascii=False)
    return hashlib.sha256(source.encode()).hexdigest()


def get_association_config() -> MemoryAssociationConfig:
    return get_openviking_config().retrieval.memory_association


async def embed_cues(embedder, texts: list[str], config, *, is_query: bool):
    # OV's account-bound embedding interface is asynchronous and single-input;
    # use bounded fan-out rather than bypassing its retries/credential routing.
    semaphore = asyncio.Semaphore(config.embedding_concurrency)

    async def embed(text):
        async with semaphore:
            result = await embed_compat(embedder, text, is_query=is_query)
            if not result.dense_vector:
                raise ValueError("Cue linking requires dense embeddings")
            return result.dense_vector

    tasks = [asyncio.create_task(embed(text)) for text in texts]
    try:
        return await asyncio.gather(*tasks)
    finally:
        # A failed/timed-out cue request must not leave sibling embeddings
        # consuming quota after the ordinary search has already returned.
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class MemoryAssociationIndex:
    """Derived cue rows in the account's existing context collection."""

    def __init__(self, storage):
        self.storage = storage
        self._memory_locks = KeyedAsyncLockPool()
        self._closing = False

    async def _backend(self, ctx):
        if self._closing or self.storage.is_closing:
            raise RuntimeError("Cue index is closing")
        backend = await self.storage.get_account_backend(ctx.account_id)
        return backend if await backend.collection_exists() else None

    async def close(self):
        # Parent storage owns backend lifecycle; never close its shared index.
        self._closing = True

    async def _delete_filter(self, ctx, filter):
        backend = await self._backend(ctx)
        if backend is None:
            return
        # Explicit account condition is needed by delete_by_filter's root API.
        await backend.delete_by_filter(
            And([Eq("account_id", ctx.account_id), association_records(), filter])
        )

    async def remove_uris(self, ctx, uris):
        if uris:
            await self._delete_filter(ctx, In("uri", list(uris)))

    async def remove_ids(self, ctx, ids):
        if ids:
            await self._delete_filter(ctx, In("tags", list(ids)))

    async def remove_uri_tree(self, ctx, uri):
        await self._delete_filter(ctx, Or([Eq("uri", uri), PathScope("uri", uri, depth=-1)]))

    async def remove_owner(self, ctx, user_id):
        await self._delete_filter(ctx, Eq("owner_user_id", user_id))

    async def remove_account(self, ctx):
        await self._delete_filter(ctx, Eq("account_id", ctx.account_id))

    async def transfer(self, ctx, source_records, target_records, *, move=False):
        """Reuse cue vectors only after the primary copy/move has committed."""
        backend = await self._backend(ctx)
        if backend is None:
            return
        pairs = {
            str(source["id"]): (source, target)
            for source, target in zip(source_records, target_records, strict=True)
            if source.get("context_type") == "memory" and source.get("level") == 2
        }
        links = []
        ids = list(pairs)
        for start in range(0, len(ids), 100):
            cursor = None
            while True:
                page, cursor = await backend.scroll(
                    filter=And([association_records(), In("tags", ids[start : start + 100])]),
                    limit=512,
                    cursor=cursor,
                )
                for row in page:
                    source, target = pairs[row["tags"]]
                    if row.get("md5") != source_fingerprint(source):
                        continue
                    if target.get("context_type") != "memory" or target.get("level") != 2:
                        continue
                    fingerprint = source_fingerprint(target)
                    identity = json.dumps([ctx.account_id, target["uri"], row["name"], fingerprint])
                    links.append(
                        {
                            **row,
                            "id": hashlib.sha256(identity.encode()).hexdigest(),
                            "uri": target["uri"],
                            "type": MEMORY_ASSOCIATION_TYPE,
                            "tags": str(target["id"]),
                            "owner_user_id": target.get("owner_user_id", ""),
                            "md5": fingerprint,
                        }
                    )
                if cursor is None:
                    break
        # Drop any overwritten destination generation even if the source has
        # no links. URI scopes are disjoint, so this cannot erase the source.
        await self.remove_uris(ctx, [target["uri"] for _, target in pairs.values()])
        for start in range(0, len(links), 100):
            batch = links[start : start + 100]
            if len(await backend.upsert_many(batch)) != len(batch):
                raise RuntimeError("Incomplete cue link transfer")
        if move:
            await self.remove_ids(ctx, ids)

    async def replace(self, record, text, embedder, ctx, config):
        if (
            record.get("context_type") != "memory"
            or record.get("level") != 2
            or record.get("type") in ASSOCIATION_RECORD_TYPES
        ):
            return
        uri = record["uri"]
        extracted = await asyncio.to_thread(extract_cues_batch, [text])
        cues = {}
        for kind, name in extracted[0][: config.max_memory_cues]:
            cues.setdefault(" ".join(name.casefold().split()), (kind, name))
        vectors = await embed_cues(
            embedder, [name for _, name in cues.values()], config, is_query=False
        )
        async with self._memory_locks.acquire((ctx.account_id, uri)):
            # A later queue item may already have replaced the primary record.
            latest = await self.storage.get_strict([record["id"]], ctx=ctx)
            fingerprint = source_fingerprint(record)
            if not latest or source_fingerprint(latest[0]) != fingerprint:
                return
            backend = await self._backend(ctx)
            if backend is None:
                return
            links = []
            for (key, (kind, name)), vector in zip(cues.items(), vectors, strict=True):
                identity = json.dumps([ctx.account_id, uri, key, fingerprint])
                links.append(
                    {
                        "id": hashlib.sha256(identity.encode()).hexdigest(),
                        "uri": uri,
                        "account_id": ctx.account_id,
                        "owner_user_id": latest[0].get("owner_user_id", ""),
                        "context_type": "memory",
                        "type": MEMORY_ASSOCIATION_TYPE,
                        "tags": str(record["id"]),
                        "level": 2,
                        "name": key,
                        "description": kind,
                        "abstract": name,
                        "md5": fingerprint,
                        "vector": vector,
                    }
                )
            if links:
                written = await backend.upsert_many(links)
                if len(written) != len(links):
                    raise RuntimeError("Incomplete cue link write")
                # Deletion may finish while the derived write is in flight.
                # Remove our generation if the parent disappeared or changed,
                # without touching a newer generation's independently keyed rows.
                current = await self.storage.get_strict([record["id"]], ctx=ctx)
                if not current or source_fingerprint(current[0]) != fingerprint:
                    await backend.strict_delete([link["id"] for link in links])
                    return
            # Insert the new generation before cleaning the old one. No vector
            # is allowed to match a mismatching memory generation during this gap.
            existing = []
            cursor = None
            while True:
                page, cursor = await backend.scroll(
                    filter=And([association_records(), Eq("uri", uri)]),
                    limit=512,
                    cursor=cursor,
                    output_fields=["id", "md5"],
                )
                existing.extend(page)
                if cursor is None:
                    break
            keep = {link["id"] for link in links}
            obsolete = [row["id"] for row in existing if row["id"] not in keep]
            if obsolete:
                await backend.strict_delete(obsolete)

    async def search(
        self, query, embedder, ctx, config, *, target_dirs=None, limit=20, score_threshold=None
    ):
        """Return cue mentions independently of ordinary memory recall."""
        backend = await self._backend(ctx)
        if backend is None:
            return {"query_cues": [], "associations": [], "total": 0}
        if backend._distance_metric != "cosine":
            raise InvalidArgumentError("Cue retrieval requires a cosine context index")
        extracted = await asyncio.to_thread(extract_cues_batch, [query])
        names = list(dict.fromkeys(name for _, name in extracted[0]))[: config.max_query_cues]
        result = {"query_cues": names, "associations": [], "total": 0}
        if not names:
            return result
        vectors = await embed_cues(embedder, names, config, is_query=True)
        threshold = config.similarity_threshold if score_threshold is None else score_threshold
        # Do not trust copied cue ACLs: live parent records are authorized below.
        conds = [Eq("account_id", ctx.account_id), association_records()]
        if target_dirs:
            conds.append(Or([PathScope("uri", uri, depth=-1) for uri in target_dirs]))
        matches = {}
        for vector in vectors:
            rows = await backend.query(
                query_vector=vector,
                filter=And(conds),
                limit=config.max_cue_matches,
                output_fields=[
                    "uri",
                    "name",
                    "abstract",
                    "description",
                    "md5",
                    "tags",
                    "account_id",
                ],
            )
            for row in rows:
                score = float(row.get("_score", 0))
                if backend._mode in {"local", "cuvs"}:
                    score = 2 * score - 1
                if not math.isfinite(score) or score < threshold:
                    continue
                key = (row.get("uri"), row.get("name"), row.get("tags"))
                if key not in matches or score > matches[key][1]:
                    matches[key] = (row, min(score, 1.0))
        if not matches:
            return result
        # Authorize current parents instead of trusting cue ACL snapshots.
        acl_enabled = await self.storage._acl_enabled(ctx)
        uris = list(dict.fromkeys(row["uri"] for row, _ in matches.values()))
        parents = {}
        for start in range(0, len(uris), 100):
            scope = self.storage._build_scope_filter(
                ctx=ctx,
                context_type="memory",
                target_directories=target_dirs,
                extra_filter=In("uri", uris[start : start + 100]),
                level=[2],
                acl_enabled=acl_enabled,
            )
            async for parent in self.storage._strict_scan(
                ctx,
                And([scope, context_records()]),
                batch_size=100,
                output_fields=["id", "uri", "md5", "abstract", "account_id"],
                what="Cue parent authorization",
            ):
                parents[str(parent["id"])] = parent
        cues = []
        for row, score in matches.values():
            parent = parents.get(str(row.get("tags", "")))
            if (
                parent is None
                or parent.get("account_id") != ctx.account_id
                or row.get("account_id") != ctx.account_id
                or parent["uri"] != row["uri"]
                or row.get("md5") != source_fingerprint(parent)
            ):
                continue
            cues.append(
                {
                    "cue": row.get("abstract") or row["name"],
                    "cue_type": row.get("description", ""),
                    "score": score,
                    "memory_uri": parent["uri"],
                }
            )
        cues.sort(key=lambda row: (-row["score"], row["cue"], row["memory_uri"]))
        result["associations"] = cues[:limit]
        result["total"] = len(result["associations"])
        return result
