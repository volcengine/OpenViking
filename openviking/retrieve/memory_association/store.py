# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Readable cue directories, with one meta.json containing all memory references.

All read/modify/write operations share a native AGFS tree lock. A small redo
journal makes multi-file changes recoverable; .index.json only holds directory
names and reverse references, never memory prose. Nothing is vectorized here.
"""

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from urllib.parse import quote, unquote

from openviking.core.memory_association import is_memory_source, memory_root
from openviking.server.error_mapping import is_not_found_error
from openviking.server.identity import Role
from openviking.session.memory.utils.memory_file_utils import (
    MemoryFileUtils,
    memory_version_from_fields,
)
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError, PermissionDeniedError

from ._cue_rules import extract_cues_batch


def normalize_cue(name: str) -> str:
    return " ".join(name.casefold().split())


def cue_directory(name: str) -> str:
    """Keep readable Unicode, spaces and case; escape unsafe bytes reversibly."""
    name = " ".join(name.split())
    if not name:
        raise ValueError("Empty association name")
    # quote() always treats dots as safe, so leading dots need explicit escaping.
    escaped = "".join(
        quote(char, safe="") if char in "/%\\\x00?#" or ord(char) < 32 else char for char in name
    )
    if escaped.startswith("."):
        escaped = "%2E" + escaped[1:]
    if len(escaped) >= 2 and escaped[0].isalpha() and escaped[1] == ":":
        escaped = escaped[0] + "%3A" + escaped[2:]
    # No hashes or truncation: reject oversized components instead of collisions.
    if len(escaped.encode("utf-8")) > 240:
        raise ValueError("Association name exceeds 240 UTF-8 bytes")
    return escaped


def fingerprint(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class FileAssociationStore:
    def __init__(self, fs, config):
        self.fs = fs
        self.config = config

    @staticmethod
    def _internal_ctx(ctx):
        # Derived metadata belongs to the source account. Returned parents are
        # separately authorized using the original caller context in search().
        return replace(ctx, role=Role.ROOT, bypass_acl=True)

    async def _json(self, uri, ctx, default=None):
        try:
            text = await self.fs.read_file(uri, ctx=ctx)
        except Exception as exc:
            if is_not_found_error(exc):
                return default
            raise
        # Corrupt metadata is an error, never interpreted as an empty index.
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError(f"Invalid association metadata: {uri}")
        return value

    async def _write_json(self, uri, value, ctx, lease):
        path = self.fs._uri_to_path(uri, ctx=ctx)
        await self.fs._ensure_parent_dirs(path, ctx=ctx, lease_ref=lease)
        await self.fs._async_agfs.write(
            path,
            json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"),
            fs_ctx=self.fs._pathlock_fs_ctx(ctx, lease),
        )

    async def _remove(self, uri, ctx, lease, *, recursive=False):
        try:
            await self.fs.remove_files(uri, recursive=recursive, ctx=ctx, lease_ref=lease)
        except Exception as exc:
            if not is_not_found_error(exc):
                raise

    @asynccontextmanager
    async def _locked(self, root, ctx, *, create=False):
        ctx = self._internal_ctx(ctx)
        if create:
            path = self.fs._uri_to_path(root + "/.index.json", ctx=ctx)
            await self.fs._ensure_parent_dirs(path, ctx=ctx)
        elif not await self.fs.exists(root, ctx=ctx):
            yield None
            return
        lease = await self.fs._async_agfs.pathlock_acquire_tree(
            self.fs._uri_to_path(root, ctx=ctx), timeout_secs=5.0
        )
        try:
            pending = await self._json(root + "/.pending.json", ctx)
            if pending is not None:
                await self._apply(root, pending, ctx, lease)
            yield lease
        finally:
            await self.fs._async_agfs.pathlock_release(lease)

    async def _state(self, root, ctx):
        state = await self._json(root + "/.index.json", ctx)
        if state is None:
            # An existing cue without a manifest must not be overwritten.
            entries = await self.fs.ls(root, output="original", node_limit=None, ctx=ctx)
            if any(not e.get("name", "").startswith(".") for e in entries):
                raise ValueError("Association directory has no .index.json; repair required")
            return {"schema_version": 1, "cues": {}, "sources": {}}
        if state.get("schema_version") != 1 or not all(
            isinstance(state.get(key), dict) for key in ("cues", "sources")
        ):
            raise ValueError("Unsupported association manifest")
        for key, directory in state["cues"].items():
            if (
                not isinstance(directory, str)
                or directory != cue_directory(unquote(directory))
                or normalize_cue(unquote(directory)) != key
            ):
                raise ValueError("Invalid association manifest directory")
        return state

    async def _apply(self, root, transaction, ctx, lease):
        for directory, meta in transaction["writes"].items():
            # Names in an internal journal are still validated before path use.
            if directory != cue_directory(meta["cue"]):
                raise ValueError("Invalid association journal directory")
            uri = root + "/" + directory
            if meta["memories"]:
                await self._write_json(uri + "/meta.json", meta, ctx, lease)
            else:
                await self._remove(uri, ctx, lease, recursive=True)
        await self._write_json(root + "/.index.json", transaction["state"], ctx, lease)
        await self._remove(root + "/.pending.json", ctx, lease)

    async def _commit(self, root, writes, state, ctx, lease):
        transaction = {"writes": writes, "state": state}
        await self._write_json(root + "/.pending.json", transaction, ctx, lease)
        await self._apply(root, transaction, ctx, lease)

    async def _source(self, uri, ctx):
        try:
            return await self.fs.read_file(uri, ctx=ctx)
        except Exception as exc:
            if is_not_found_error(exc):
                return None
            raise

    async def refresh(self, uri, ctx):
        """Idempotent repair/build for one memory; always reads the live source."""
        if not is_memory_source(uri):
            return
        ctx = self._internal_ctx(ctx)
        root = memory_root(uri) + "/.association"
        raw = await self._source(uri, ctx)
        cues = {}
        if raw is not None:
            memory = MemoryFileUtils.read(raw, uri=uri)
            extracted = await asyncio.to_thread(
                extract_cues_batch, [memory.content], model_name=self.config.nlp_model
            )
            for kind, name in extracted[0]:
                key = normalize_cue(name)
                if not key or key in cues:
                    continue
                try:
                    directory = cue_directory(name)
                except ValueError:
                    continue
                cues[key] = (kind, " ".join(name.split()), directory)
                if len(cues) >= self.config.max_memory_cues:
                    break
            reference = {
                "uri": uri,
                "source_version": memory_version_from_fields(memory.extra_fields),
                "source_fingerprint": fingerprint(raw),
            }
        async with self._locked(root, ctx, create=raw is not None) as lease:
            if lease is None:
                return
            # A delayed old job must not publish stale references over a new write.
            if await self._source(uri, ctx) != raw:
                raise RuntimeError("Association source changed; retry refresh")
            state = await self._state(root, ctx)
            writes = {}
            old = state["sources"].get(uri, [])
            for key in sorted(set(old) | set(cues)):
                directory = state["cues"].get(key)
                if directory:
                    meta = await self._json(root + "/" + directory + "/meta.json", ctx)
                    if meta is None or normalize_cue(meta.get("cue", "")) != key:
                        raise ValueError("Missing or inconsistent association metadata")
                else:
                    kind, name, directory = cues[key]
                    meta = {"cue": name, "cue_type": kind, "memories": []}
                refs = [ref for ref in meta["memories"] if ref["uri"] != uri]
                if key in cues:
                    refs.append(reference)
                meta["memories"] = sorted(refs, key=lambda ref: ref["uri"])
                writes[directory] = meta
                if refs:
                    state["cues"][key] = directory
                else:
                    state["cues"].pop(key, None)
            if cues:
                state["sources"][uri] = sorted(cues)
            else:
                state["sources"].pop(uri, None)
            await self._commit(root, writes, state, ctx, lease)

    async def refresh_tree(self, uri, ctx):
        """Reconcile copies/moves/deletes without retaining obsolete references."""
        root = memory_root(uri)
        if root is None:
            return
        if is_memory_source(uri):
            await self.refresh(uri, ctx)
            return
        internal = self._internal_ctx(ctx)
        association = root + "/.association"
        previous = []
        async with self._locked(association, internal) as lease:
            if lease is not None:
                state = await self._state(association, internal)
                # A whole memories-root copy carries derived JSON as files.
                # Remove source-owner references from that copy before adding
                # destination-owner references; never mutate the source tree.
                foreign = {source for source in state["sources"] if memory_root(source) != root}
                if foreign:
                    writes = {}
                    keys = {key for source in foreign for key in state["sources"][source]}
                    for key in keys:
                        directory = state["cues"][key]
                        meta = await self._json(
                            association + "/" + directory + "/meta.json", internal
                        )
                        if meta is None:
                            raise ValueError("Missing copied association metadata")
                        meta["memories"] = [
                            ref for ref in meta["memories"] if ref["uri"] not in foreign
                        ]
                        writes[directory] = meta
                        if not meta["memories"]:
                            state["cues"].pop(key, None)
                    for source in foreign:
                        state["sources"].pop(source)
                    await self._commit(association, writes, state, internal, lease)
                previous = [
                    source
                    for source in state["sources"]
                    if source == uri or source.startswith(uri.rstrip("/") + "/")
                ]
        live = []
        if await self.fs.exists(uri, ctx=internal):
            entries = await self.fs.tree(
                uri, output="original", node_limit=None, level_limit=None, ctx=internal
            )
            live = [entry["uri"] for entry in entries if is_memory_source(entry["uri"])]
        for source in sorted(set(previous) | set(live)):
            await self.refresh(source, internal)

    async def search(self, query, targets, ctx, limit=20):
        """Exact normalized cue lookup; score=1 means an exact cue hit, not cosine."""
        extracted = await asyncio.to_thread(
            extract_cues_batch, [query], model_name=self.config.nlp_model
        )
        keys = list(dict.fromkeys(normalize_cue(name) for _, name in extracted[0]))
        keys = keys[: self.config.max_query_cues]
        if not keys:
            return []
        roots = {}
        for target in targets:
            root = memory_root(target)
            if root is None:
                raise InvalidArgumentError("Association target must be a user memories path")
            roots.setdefault(root, []).append(target.rstrip("/"))
        result = []
        # Limit applies to distinct memory URIs, preserving all matched cues.
        accepted = set()
        source_cache = {}
        for root, scopes in roots.items():
            internal = self._internal_ctx(ctx)
            association = root + "/.association"
            candidates = []
            async with self._locked(association, internal) as lease:
                if lease is None:
                    continue
                state = await self._state(association, internal)
                for key in keys:
                    directory = state["cues"].get(key)
                    if not directory:
                        continue
                    meta = await self._json(association + "/" + directory + "/meta.json", internal)
                    if meta is None:
                        raise ValueError("Missing association metadata")
                    for ref in meta["memories"]:
                        if memory_root(ref["uri"]) == root and is_memory_source(ref["uri"]):
                            candidates.append((meta["cue"], meta["cue_type"], ref))
            for name, kind, ref in candidates:
                uri = ref["uri"]
                if not any(uri == scope or uri.startswith(scope + "/") for scope in scopes):
                    continue
                if uri not in accepted and len(accepted) >= limit:
                    continue
                if uri not in source_cache:
                    try:
                        await self.fs._ensure_retrieval_scope(uri, ctx)
                        source_cache[uri] = await self.fs.read_file(uri, ctx=ctx)
                    except (NotFoundError, PermissionDeniedError):
                        source_cache[uri] = None
                raw = source_cache[uri]
                if raw is None or fingerprint(raw) != ref["source_fingerprint"]:
                    continue
                accepted.add(uri)
                result.append({"cue": name, "cue_type": kind, "score": 1.0, "memory_uri": uri})
                if len(result) >= self.config.max_cue_matches:
                    return result
        return result
