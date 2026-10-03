# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Derived entity vectors pointing to existing memory files, not new memories.

One row per (memory, entity, source fingerprint) avoids concurrent mutation of
shared linked-memory lists. Ranking only considers the already authorized recall
candidates and checks their source fingerprints, including after ACL changes.
"""

import asyncio
import hashlib
import json
import math
from collections import defaultdict

from openviking.models.embedder.base import embed_compat
from openviking.service.task_tracker_concurrency import KeyedAsyncLockPool
from openviking.storage.expr import And, Eq, In, Or, PathScope
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.config.retrieval_config import EntityLinkingConfig

from ._entity_rules import extract_entities_batch


def source_fingerprint(record: dict) -> str:
    """Do not let a previous version of a memory influence the current result."""
    source = json.dumps([record.get("md5", ""), record.get("abstract", "")], ensure_ascii=False)
    return hashlib.sha256(source.encode()).hexdigest()


def linking_config() -> EntityLinkingConfig:
    return get_openviking_config().retrieval.entity_linking


async def embed_entities(embedder, texts: list[str], config, *, is_query: bool):
    # OV's account-bound embedding interface is asynchronous and single-input;
    # use bounded fan-out rather than bypassing its retries/credential routing.
    semaphore = asyncio.Semaphore(config.embedding_concurrency)

    async def embed(text):
        async with semaphore:
            result = await embed_compat(embedder, text, is_query=is_query)
            if not result.dense_vector:
                raise ValueError("Entity linking requires dense embeddings")
            return result.dense_vector

    tasks = [asyncio.create_task(embed(text)) for text in texts]
    try:
        return await asyncio.gather(*tasks)
    finally:
        # A failed/timed-out entity request must not leave sibling embeddings
        # consuming quota after the ordinary search has already returned.
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class EntityLinkIndex:
    """Account-bound auxiliary collection using the configured vector backend."""

    def __init__(self, storage):
        self.storage = storage
        self._backends = {}
        self._account_locks = KeyedAsyncLockPool()
        self._memory_locks = KeyedAsyncLockPool()
        self._closing = False

    async def _backend(self, ctx, *, create=False):
        from openviking.storage.collection_schemas import CollectionSchemas
        from openviking.storage.viking_vector_index_backend import _SingleAccountBackend

        parent = await self.storage.get_account_backend(ctx.account_id)
        async with self._account_locks.acquire(ctx.account_id):
            if self._closing or self.storage.is_closing:
                raise RuntimeError("Entity index is closing")
            cached = self._backends.get(ctx.account_id)
            if cached is not None and cached[0] is not parent:
                await cached[1].close()
                self._backends.pop(ctx.account_id)
                cached = None
            if cached is not None:
                return cached[1]
            settings = await self.storage._resolve_vector_settings(ctx.account_id)
            config = settings.vectordb.model_copy(deep=True)
            if config.backend == "volcengine" and config.volcengine.api_key:
                if create:
                    raise ValueError("Entity linking needs collection-management credentials")
                return None
            account_suffix = hashlib.sha256(ctx.account_id.encode()).hexdigest()[:16]
            config.name = parent.collection_name + "_entity_links_" + account_suffix
            config.dimension = parent.vector_dim
            config.distance_metric = "cosine"
            config.sparse_weight = 0
            backend = _SingleAccountBackend(config, bound_account_id=ctx.account_id)
            try:
                if not await backend.collection_exists():
                    if not create:
                        await backend.close()
                        return None
                    parent_meta = await parent.get_collection_meta() or {}
                    description = "Entity mention links\n" + parent_meta.get("Description", "")
                    created = await backend.create_collection(
                        config.name,
                        CollectionSchemas.context_collection(
                            config.name, config.dimension, description=description
                        ),
                    )
                    if not created:
                        raise RuntimeError("Could not create entity link collection")
                meta = await backend.get_collection_meta() or {}
                actual_dim = next(
                    (
                        f.get("Dim")
                        for f in meta.get("Fields", [])
                        if f.get("FieldName") == "vector"
                    ),
                    None,
                )
                parent_meta = await parent.get_collection_meta() or {}
                expected = "Entity mention links\n" + parent_meta.get("Description", "")
                if actual_dim != config.dimension or meta.get("Description") != expected:
                    raise ValueError(
                        "Entity index embedding configuration changed; rebuild required"
                    )
            except BaseException:
                await backend.close()
                raise
            self._backends[ctx.account_id] = (parent, backend)
            return backend

    async def close(self):
        self._closing = True
        backends = list(self._backends.values())
        self._backends.clear()
        for _, backend in backends:
            await backend.close()

    async def release_account(self, account_id):
        async with self._account_locks.acquire(account_id):
            cached = self._backends.pop(account_id, None)
            if cached:
                await cached[1].close()

    async def _delete_filter(self, ctx, filter):
        backend = await self._backend(ctx)
        if backend is None:
            return
        # Explicit account condition is needed by delete_by_filter's root API.
        await backend.delete_by_filter(And([Eq("account_id", ctx.account_id), filter]))

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
        """Reuse entity vectors only after the primary copy/move has committed."""
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
            offset = 0
            while True:
                page = await backend.strict_query(
                    filter=In("tags", ids[start : start + 100]), limit=512, offset=offset
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
                            "tags": str(target["id"]),
                            "owner_user_id": target.get("owner_user_id", ""),
                            "md5": fingerprint,
                        }
                    )
                if len(page) < 512:
                    break
                offset += len(page)
        # Drop any overwritten destination generation even if the source has
        # no links. URI scopes are disjoint, so this cannot erase the source.
        await self.remove_uris(ctx, [target["uri"] for _, target in pairs.values()])
        for start in range(0, len(links), 100):
            batch = links[start : start + 100]
            if len(await backend.upsert_many(batch)) != len(batch):
                raise RuntimeError("Incomplete entity link transfer")
        if move:
            await self.remove_ids(ctx, ids)

    async def replace(self, record, text, embedder, ctx, config):
        if record.get("context_type") != "memory" or record.get("level") != 2:
            return
        uri = record["uri"]
        extracted = await asyncio.to_thread(extract_entities_batch, [text])
        entities = {}
        for kind, name in extracted[0][: config.max_memory_entities]:
            entities.setdefault(" ".join(name.casefold().split()), (kind, name))
        vectors = await embed_entities(
            embedder, [name for _, name in entities.values()], config, is_query=False
        )
        async with self._memory_locks.acquire((ctx.account_id, uri)):
            # A later queue item may already have replaced the primary record.
            latest = await self.storage.get_strict([record["id"]], ctx=ctx)
            fingerprint = source_fingerprint(record)
            if not latest or source_fingerprint(latest[0]) != fingerprint:
                return
            backend = await self._backend(ctx, create=bool(entities))
            if backend is None:
                return
            links = []
            for (key, (kind, name)), vector in zip(entities.items(), vectors, strict=True):
                identity = json.dumps([ctx.account_id, uri, key, fingerprint])
                links.append(
                    {
                        "id": hashlib.sha256(identity.encode()).hexdigest(),
                        "uri": uri,
                        "account_id": ctx.account_id,
                        "owner_user_id": latest[0].get("owner_user_id", ""),
                        "context_type": "memory",
                        "type": "entity_link",
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
                    raise RuntimeError("Incomplete entity link write")
                # Deletion may finish while the derived write is in flight.
                # Remove our generation if the parent disappeared or changed,
                # without touching a newer generation's independently keyed rows.
                current = await self.storage.get_strict([record["id"]], ctx=ctx)
                if not current or source_fingerprint(current[0]) != fingerprint:
                    await backend.strict_delete([link["id"] for link in links])
                    return
            # Insert the new generation before cleaning the old one. No vector
            # is allowed to boost a mismatching memory generation during this gap.
            existing = []
            offset = 0
            while True:
                page = await backend.strict_query(
                    filter=Eq("uri", uri), limit=512, offset=offset, output_fields=["id", "md5"]
                )
                existing.extend(page)
                if len(page) < 512:
                    break
                offset += len(page)
            keep = {link["id"] for link in links}
            obsolete = [row["id"] for row in existing if row["id"] not in keep]
            if obsolete:
                await backend.strict_delete(obsolete)

    async def boosts(self, query, candidates, embedder, ctx, config):
        # Candidates were scoped by the ordinary index using live ACLs, target
        # directories, metadata filters, and result levels. Never widen this set.
        eligible = {
            row["uri"]: row
            for row in candidates
            if row.get("context_type") == "memory" and row.get("level") == 2
        }
        if not eligible:
            return {}
        backend = await self._backend(ctx)
        if backend is None:
            return {}
        extracted = await asyncio.to_thread(extract_entities_batch, [query])
        names = list(dict.fromkeys(name for _, name in extracted[0]))[: config.max_query_entities]
        vectors = await embed_entities(embedder, names, config, is_query=True)
        boosts = {}
        for vector in vectors:
            matches = await backend.query(
                query_vector=vector,
                filter=In("uri", list(eligible)),
                limit=min(len(eligible) * config.max_memory_entities, config.max_entity_matches),
                output_fields=["uri", "name", "md5", "account_id"],
            )
            groups = defaultdict(list)
            for match in matches:
                parent = eligible.get(match.get("uri"))
                if parent is None or match.get("account_id") != ctx.account_id:
                    continue
                if match.get("md5") != source_fingerprint(parent):
                    continue
                similarity = float(match.get("_score", 0))
                if backend._mode in {"local", "cuvs"}:
                    # These adapters expose (cosine + 1) / 2. The entity
                    # threshold/boost uses cosine, as in the source algorithm.
                    similarity = 2 * similarity - 1
                if not math.isfinite(similarity) or similarity < config.similarity_threshold:
                    continue
                groups[match.get("name", "")].append((parent["uri"], min(similarity, 1)))
            for links in groups.values():
                # Frequency is measured only among authorized recall candidates;
                # hidden memories must not influence even the reported score.
                degree = len({uri for uri, _ in links})
                penalty = 1 / (1 + 0.001 * (degree - 1) ** 2)
                for uri, similarity in links:
                    boost = config.weight * similarity * penalty
                    boosts[uri] = max(boosts.get(uri, 0), boost)
        return boosts
