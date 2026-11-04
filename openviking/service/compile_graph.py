# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Artifact relation graph construction and cache filtering."""

from __future__ import annotations

import asyncio
import hashlib
import posixpath
import time
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt
from pathspec import GitIgnoreSpec

from openviking.pyagfs.exceptions import AGFSNotFoundError, AGFSResourceExhaustedError
from openviking.server.identity import RequestContext, Role
from openviking.storage.acl import AclAction
from openviking_cli.exceptions import OpenVikingError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

SCHEMA_VERSION = 1
PARSER_VERSION = 1
MAX_RECONCILE_ATTEMPTS = 3
_DERIVED_NAMES = frozenset({".abstract.md", ".overview.md", ".relations.json"})


class ArtifactGraphError(OpenVikingError):
    def __init__(self, message: str, code: str, details: dict[str, Any] | None = None):
        super().__init__(message, code=code, details=details)


class ArtifactGraphTooLargeError(ArtifactGraphError):
    def __init__(self, limit: str, maximum: int, actual: int):
        super().__init__(
            "Artifact relation graph exceeds the configured limit",
            code="ARTIFACT_RELATIONS_TOO_LARGE",
            details={"limit": limit, "maximum": maximum, "actual": actual},
        )


class ArtifactRelationGraph:
    """Build one target's graph once, then filter the cached result per caller."""

    def __init__(
        self,
        store: Any,
        viking_fs: Any,
        *,
        max_nodes: int,
        max_markdown_files: int,
        max_markdown_bytes: int,
        max_relations: int = 100000,
        compile_wait_seconds: float = 60.0,
        query_wait_seconds: float = 10.0,
    ) -> None:
        self._store = store
        self._fs = viking_fs
        self._max_nodes = max_nodes
        self._max_relations = max_relations
        self._max_markdown_files = max_markdown_files
        self._max_markdown_bytes = max_markdown_bytes
        self._compile_wait_seconds = compile_wait_seconds
        self._query_wait_seconds = query_wait_seconds
        self._markdown = MarkdownIt("commonmark")
        self._compile_pages: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = {}
        self._compile_relation_counts: dict[tuple[str, str, str], int] = {}
        self._compile_byte_counts: dict[tuple[str, str, str], int] = {}
        self._compile_hint_overflow: set[tuple[str, str, str]] = set()
        self._background: set[asyncio.Task[Any]] = set()

    async def get(
        self,
        record: dict[str, Any],
        ctx: RequestContext,
        *,
        lock_output: Any,
        commit_snapshot: Any,
    ) -> dict[str, Any]:
        if not record.get("initialized"):
            raise ArtifactGraphError(
                "Compile target is not initialized",
                code="COMPILE_TARGET_NOT_INITIALIZED",
            )
        wiki_head = record.get("wiki_head")
        if not wiki_head:
            async with lock_output(record["to"]) as lease:
                record = await self._store.get(ctx.account_id, record["to"])
                wiki_head = record.get("wiki_head")
                if not wiki_head:
                    wiki_head = await commit_snapshot(record["to"], lease)
                    if not wiki_head:
                        raise ArtifactGraphError(
                            "Snapshots are disabled",
                            code="SNAPSHOT_DISABLED",
                        )
                    async with self._store.edit(ctx.account_id, record["to"]) as editable:
                        editable["wiki_head"] = wiki_head
                    record["wiki_head"] = wiki_head

        _, ignore_signature = await self._ignore_state(self._internal_ctx(ctx))
        cached = await self._read_cache(ctx.account_id, record["to"])
        if await self._cache_current(
            cached,
            record["to"],
            wiki_head,
            ctx,
            ignore_signature=ignore_signature,
        ):
            return await self._response(cached, ctx, stale=False, building=False)

        state = await self._read_state(ctx.account_id, record["to"])
        state_current = self._state_matches(state, wiki_head, ignore_signature)
        if state_current and state.get("status") == "failed":
            self._raise_state_error(state)
        if not state_current or state.get("status") != "building":
            await self.enqueue_wakeup(ctx.account_id, record["to"], ctx.user.user_id)
        deadline = time.monotonic() + self._query_wait_seconds
        delay = 0.2
        ready_seen: tuple[Any, Any] | None = None
        while time.monotonic() < deadline:
            await asyncio.sleep(min(delay, max(0.0, deadline - time.monotonic())))
            delay = min(delay * 1.5, 1.0)
            state = await self._read_state(ctx.account_id, record["to"])
            if not isinstance(state, dict):
                continue
            state_head = state.get("target_snapshot_id")
            if not self._state_matches(state, wiki_head, ignore_signature):
                latest = await self._store.get(ctx.account_id, record["to"])
                latest_head = (latest or record).get("wiki_head")
                if latest_head and latest_head != wiki_head:
                    wiki_head = latest_head
                    await self.enqueue_wakeup(
                        ctx.account_id,
                        record["to"],
                        ctx.user.user_id,
                    )
                continue
            if state.get("status") == "failed":
                self._raise_state_error(state)
            if state.get("status") != "ready":
                continue
            ready_version = (state_head, state.get("updated_at"))
            if ready_version == ready_seen:
                continue
            ready_seen = ready_version
            current = await self._read_cache(ctx.account_id, record["to"])
            if await self._cache_current(
                current,
                record["to"],
                wiki_head,
                ctx,
                ignore_signature=ignore_signature,
            ):
                return await self._response(current, ctx, stale=False, building=False)

        current = await self._read_cache(ctx.account_id, record["to"])
        if await self._cache_current(current, record["to"], wiki_head, ctx):
            return await self._response(current, ctx, stale=False, building=False)
        if self._cache_valid(cached, record["to"]):
            return await self._response(cached, ctx, stale=True, building=True)
        return {
            "schema_version": SCHEMA_VERSION,
            "to": record["to"].rstrip("/"),
            "target_snapshot_id": wiki_head,
            "nodes": [],
            "edges": [],
            "stale": False,
            "building": True,
        }

    async def reconcile_graph(
        self,
        record: dict[str, Any],
        ctx: RequestContext,
        *,
        wiki_head: str,
        parsed_pages: dict[str, dict[str, Any]] | None = None,
        attempts: int = 1,
    ) -> dict[str, Any] | None:
        """Reconcile one target to its latest immutable Snapshot."""
        async with self._store.lock_artifact_graph(ctx.account_id, record["to"]):
            current = await self._store.get(ctx.account_id, record["to"])
            if not self._record_active(current):
                return None
            head = (current or record).get("wiki_head") or wiki_head
            internal_ctx = self._internal_ctx(ctx)
            ignore, ignore_signature = await self._ignore_state(internal_ctx)
            await self._write_state(
                ctx.account_id,
                record["to"],
                head,
                status="building",
                attempts=attempts,
                ignore_signature=ignore_signature,
            )
            try:
                result = await self._reconcile_once(
                    record["to"],
                    head,
                    internal_ctx,
                    parsed_pages=parsed_pages,
                    ignore=ignore,
                    ignore_signature=ignore_signature,
                )
            except ArtifactGraphError as exc:
                await self._write_failed_state(
                    ctx.account_id,
                    record["to"],
                    head,
                    exc,
                    attempts=attempts,
                    ignore_signature=ignore_signature,
                )
                raise
            except Exception as exc:
                if attempts >= MAX_RECONCILE_ATTEMPTS:
                    await self._write_failed_state(
                        ctx.account_id,
                        record["to"],
                        head,
                        exc,
                        attempts=attempts,
                        ignore_signature=ignore_signature,
                    )
                raise
            await self._write_state(
                ctx.account_id,
                record["to"],
                head,
                status="ready",
                attempts=attempts,
                ignore_signature=ignore_signature,
            )
            return result

    async def reconcile_compile(
        self,
        record: dict[str, Any],
        ctx: RequestContext,
        *,
        wiki_head: str,
        operation_id: str,
    ) -> None:
        """Wait briefly for the graph without making Compile depend on it."""
        key = (ctx.account_id, record["to"], operation_id)
        parsed_pages = self._compile_pages.pop(key, None)
        self._compile_relation_counts.pop(key, None)
        self._compile_byte_counts.pop(key, None)
        self._compile_hint_overflow.discard(key)
        task = asyncio.create_task(
            self.reconcile_graph(
                record,
                ctx,
                wiki_head=wiki_head,
                parsed_pages=parsed_pages,
            )
        )
        self._background.add(task)
        task.add_done_callback(self._finish_background)
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=self._compile_wait_seconds)
        except TimeoutError:
            await self.enqueue_wakeup(ctx.account_id, record["to"], ctx.user.user_id)
        except Exception:
            logger.exception("Artifact relation graph build failed for %s", record["to"])
            await self.enqueue_wakeup(ctx.account_id, record["to"], ctx.user.user_id)

    async def capture_compile_page(
        self,
        account_id: str,
        to: str,
        operation_id: str,
        uri: str,
        content: bytes | None = None,
    ) -> None:
        """Parse Compile bytes now and validate them against the final tree later."""
        if not uri.lower().endswith(".md") or self._excluded(uri, None):
            return
        key = (account_id, to, operation_id)
        if content is None:
            pages = self._compile_pages.get(key)
            if pages is not None:
                previous = pages.pop(uri, None)
                if isinstance(previous, dict):
                    self._compile_relation_counts[key] = max(
                        0,
                        self._compile_relation_counts.get(key, 0)
                        - len(previous.get("related_pages") or ()),
                    )
                    self._compile_byte_counts[key] = max(
                        0,
                        self._compile_byte_counts.get(key, 0)
                        - int(previous.get("size") or 0),
                    )
            return
        blob_oid = hashlib.sha1(
            f"blob {len(content)}\0".encode("ascii") + content,
            usedforsecurity=False,
        ).hexdigest()
        size = len(content)
        if key in self._compile_hint_overflow:
            return
        pages = self._compile_pages.setdefault(key, {})
        previous = pages.get(uri)
        if previous is None and len(pages) >= self._max_markdown_files:
            self._compile_hint_overflow.add(key)
            return
        count = self._compile_relation_counts.get(key, 0)
        byte_count = self._compile_byte_counts.get(key, 0)
        if isinstance(previous, dict):
            count -= len(previous.get("related_pages") or ())
            byte_count -= int(previous.get("size") or 0)
        if byte_count + size > self._max_markdown_bytes:
            self._compile_hint_overflow.add(key)
            return
        remaining = max(0, self._max_relations - count)
        text = content.decode("utf-8", errors="replace")
        related_pages, overflow = self._related_pages(
            text,
            uri,
            target_root=to,
            maximum=remaining,
        )
        pages[uri] = {
            "blob_oid": blob_oid,
            "size": size,
            "related_pages": sorted(related_pages),
            "relations_overflow": overflow,
        }
        self._compile_relation_counts[key] = count + len(related_pages)
        self._compile_byte_counts[key] = byte_count + size

    def discard_compile_pages(self, account_id: str, to: str, operation_id: str) -> None:
        key = (account_id, to, operation_id)
        self._compile_pages.pop(key, None)
        self._compile_relation_counts.pop(key, None)
        self._compile_byte_counts.pop(key, None)
        self._compile_hint_overflow.discard(key)

    def _finish_background(self, task: asyncio.Task[Any]) -> None:
        self._background.discard(task)
        if not task.cancelled():
            task.exception()

    async def reconcile_target(
        self,
        account_id: str,
        to: str,
        user_id: str,
        *,
        attempt: int = 0,
    ) -> None:
        """Worker entrypoint; chase wiki_head until the target is current."""
        ctx = RequestContext(
            user=UserIdentifier(account_id=account_id, user_id=user_id or "artifact-graph"),
            role=Role.USER,
            bypass_acl=True,
        )
        while True:
            record = await self._store.get(account_id, to)
            if (
                not self._record_active(record)
                or not record.get("initialized")
                or not record.get("wiki_head")
            ):
                return
            await self.reconcile_graph(
                record,
                ctx,
                wiki_head=record["wiki_head"],
                attempts=attempt + 1,
            )
            latest = await self._store.get(account_id, to)
            if not self._record_active(latest):
                return
            cached = await self._read_cache(account_id, to)
            if latest is None or await self._cache_current(
                cached,
                to,
                latest.get("wiki_head"),
                ctx,
            ):
                return

    async def enqueue_wakeup(
        self,
        account_id: str,
        to: str,
        user_id: str,
        *,
        attempt: int = 0,
    ) -> bool:
        try:
            from openviking.storage.queuefs import get_queue_manager
            from openviking.storage.queuefs.queue_manager import QueueManager

            payload: dict[str, Any] = {
                "account_id": account_id,
                "to": to,
                "user_id": user_id,
            }
            if attempt:
                payload["attempt"] = attempt
            await get_queue_manager().enqueue(
                QueueManager.ARTIFACT_RELATIONS,
                payload,
            )
            return True
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to enqueue artifact relation graph for %s", to)
            return False

    async def mark_failed(
        self,
        account_id: str,
        to: str,
        error: Exception,
        *,
        attempts: int,
    ) -> None:
        """Persist a terminal build failure without replacing the last good graph."""
        async with self._store.lock_artifact_graph(account_id, to):
            record = await self._store.get(account_id, to)
            if not self._record_active(record) or not record.get("wiki_head"):
                return
            state = await self._read_state(account_id, to)
            if isinstance(state, dict) and state.get("status") in {"ready", "failed"}:
                return
            await self._write_failed_state(
                account_id,
                to,
                record["wiki_head"],
                error,
                attempts=attempts,
                ignore_signature=state.get("ignore_signature")
                if isinstance(state, dict)
                else None,
            )

    async def _reconcile_once(
        self,
        to: str,
        head: str,
        ctx: RequestContext,
        *,
        parsed_pages: dict[str, dict[str, Any]] | None,
        ignore: GitIgnoreSpec | None,
        ignore_signature: str,
    ) -> dict[str, Any]:
        to = to.rstrip("/")
        graph = await self._read_cache(ctx.account_id, to)
        sources = await self._read_sources(ctx.account_id, to)
        base = None
        if (
            self._cache_valid(graph, to)
            and isinstance(sources, dict)
            and sources.get("schema_version") == SCHEMA_VERSION
            and sources.get("parser_version") == PARSER_VERSION
            and sources.get("ignore_signature") == ignore_signature
            and sources.get("to") == to
            and sources.get("target_snapshot_id") == graph.get("target_snapshot_id")
            and isinstance(sources.get("pages"), dict)
            and isinstance(sources.get("page_sizes"), dict)
        ):
            base = graph["target_snapshot_id"]
        if base == head:
            return graph

        changes = await self._fs.diff_paths(
            dirs=[to],
            base=base,
            head=head,
            include_blob_oids=True,
            ctx=ctx,
        )
        files = (
            {node["uri"] for node in graph["nodes"] if not node["uri"].endswith("/")}
            if base
            else set()
        )
        pages = dict(sources["pages"]) if base else {}
        page_sizes = dict(sources["page_sizes"]) if base else {}
        head_blob_oids: dict[str, str] = {}
        changed_markdown: list[str] = []
        for change in changes:
            uri = change["uri"]
            if change["change_type"] == "deleted" or self._excluded(uri, ignore):
                files.discard(uri)
                pages.pop(uri, None)
                page_sizes.pop(uri, None)
                continue
            files.add(uri)
            if uri.lower().endswith(".md"):
                changed_markdown.append(uri)
                if change.get("blob_oid"):
                    head_blob_oids[uri] = change["blob_oid"]
            else:
                pages.pop(uri, None)
                page_sizes.pop(uri, None)

        nodes = files | self._required_directories(to, files)
        self._check_limit("nodes", self._max_nodes, len(nodes))
        changed = set(changed_markdown)
        self._check_limit(
            "markdown_files",
            self._max_markdown_files,
            len(set(pages) | changed),
        )
        edges: set[tuple[str, str, str]] = set()
        for edge in self._containment_edges(to, nodes):
            self._add_relation(edges, edge)

        markdown_bytes = sum(
            int(size) for uri, size in page_sizes.items() if uri not in changed
        )
        self._check_limit("markdown_bytes", self._max_markdown_bytes, markdown_bytes)
        page_order = list(dict.fromkeys(changed_markdown))
        page_order.extend(sorted(set(pages) - changed))
        for uri in page_order:
            if uri in changed:
                candidate = (parsed_pages or {}).get(uri)
                candidate_valid = False
                if (
                    isinstance(candidate, dict)
                    and isinstance(candidate.get("blob_oid"), str)
                    and candidate["blob_oid"]
                    and candidate.get("blob_oid") == head_blob_oids.get(uri)
                    and not candidate.get("relations_overflow")
                ):
                    candidate_valid = True
                if candidate_valid and not isinstance(candidate.get("related_pages"), list):
                    candidate_valid = False
                if candidate_valid:
                    blob_oid = candidate["blob_oid"]
                    page_size = int(candidate.get("size") or 0)
                    self._check_limit(
                        "markdown_bytes",
                        self._max_markdown_bytes,
                        markdown_bytes + page_size,
                    )
                    related_pages = self._valid_targets(candidate["related_pages"], nodes)
                else:
                    try:
                        blob = await self._fs.show_blob_raw(
                            head,
                            path=uri,
                            ctx=ctx,
                            max_blob_bytes=max(0, self._max_markdown_bytes - markdown_bytes),
                        )
                    except AGFSResourceExhaustedError as exc:
                        raise ArtifactGraphTooLargeError(
                            "markdown_bytes",
                            self._max_markdown_bytes,
                            self._max_markdown_bytes + 1,
                        ) from exc
                    blob_oid = blob["oid"]
                    page_size = int(blob.get("size") or len(blob["bytes"]))
                    self._check_limit(
                        "markdown_bytes",
                        self._max_markdown_bytes,
                        markdown_bytes + page_size,
                    )
                    text = blob["bytes"].decode("utf-8", errors="replace")
                    related, overflow = self._related_pages(
                        text,
                        uri,
                        valid_nodes=nodes,
                        maximum=max(0, self._max_relations - len(edges)),
                    )
                    if overflow:
                        raise ArtifactGraphTooLargeError(
                            "relations",
                            self._max_relations,
                            self._max_relations + 1,
                        )
                    related_pages = sorted(related)
                markdown_bytes += page_size
                pages[uri] = {
                    "blob_oid": blob_oid,
                    "related_pages": related_pages,
                }
                page_sizes[uri] = page_size
            else:
                related_pages = self._valid_targets(
                    pages[uri].get("related_pages") or (),
                    nodes,
                )
                pages[uri] = {
                    "blob_oid": pages[uri].get("blob_oid"),
                    "related_pages": related_pages,
                }

            for target in related_pages:
                self._add_relation(edges, ("references", uri, target))

        self._check_limit(
            "markdown_bytes",
            self._max_markdown_bytes,
            markdown_bytes,
        )
        generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        sources_value = {
            "schema_version": SCHEMA_VERSION,
            "parser_version": PARSER_VERSION,
            "ignore_signature": ignore_signature,
            "to": to,
            "target_snapshot_id": head,
            "generated_at": generated_at,
            "pages": dict(sorted(pages.items())),
            "page_sizes": dict(sorted(page_sizes.items())),
        }
        result = {
            "schema_version": SCHEMA_VERSION,
            "to": to,
            "target_snapshot_id": head,
            "generated_at": generated_at,
            "nodes": [{"uri": uri} for uri in sorted(nodes)],
            "edges": [
                {"type": edge_type, "from": source, "to": target}
                for edge_type, source, target in sorted(edges)
            ],
        }
        await self._store.write_relation_sources(ctx.account_id, to, sources_value)
        await self._store.write_artifact_relations(ctx.account_id, to, result)
        return result

    async def _read_cache(self, account_id: str, to: str) -> dict[str, Any] | None:
        try:
            return await self._store.read_artifact_relations(account_id, to)
        except (ValueError, UnicodeDecodeError):
            return None

    async def _read_sources(self, account_id: str, to: str) -> dict[str, Any] | None:
        try:
            return await self._store.read_relation_sources(account_id, to)
        except (ValueError, UnicodeDecodeError):
            return None

    async def _read_state(self, account_id: str, to: str) -> dict[str, Any] | None:
        try:
            return await self._store.read_artifact_relations_state(account_id, to)
        except (ValueError, UnicodeDecodeError):
            return None

    async def _write_state(
        self,
        account_id: str,
        to: str,
        head: str,
        *,
        status: str,
        attempts: int,
        ignore_signature: str | None,
        error: dict[str, Any] | None = None,
    ) -> None:
        await self._store.write_artifact_relations_state(
            account_id,
            to,
            {
                "schema_version": SCHEMA_VERSION,
                "parser_version": PARSER_VERSION,
                "ignore_signature": ignore_signature,
                "target_snapshot_id": head,
                "status": status,
                "attempts": attempts,
                "updated_at": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "error": error,
            },
        )

    async def _write_failed_state(
        self,
        account_id: str,
        to: str,
        head: str,
        error: Exception,
        *,
        attempts: int,
        ignore_signature: str | None,
    ) -> None:
        if isinstance(error, ArtifactGraphError):
            code = error.code
            details = error.details
        else:
            code = "ARTIFACT_RELATIONS_BUILD_FAILED"
            details = None
        await self._write_state(
            account_id,
            to,
            head,
            status="failed",
            attempts=attempts,
            ignore_signature=ignore_signature,
            error={
                "code": code,
                "message": str(error),
                "details": details,
            },
        )

    @staticmethod
    def _state_matches(state: Any, head: Any, ignore_signature: str) -> bool:
        return bool(
            isinstance(state, dict)
            and state.get("schema_version") == SCHEMA_VERSION
            and state.get("parser_version") == PARSER_VERSION
            and state.get("ignore_signature") == ignore_signature
            and state.get("target_snapshot_id") == head
        )

    @staticmethod
    def _record_active(record: Any) -> bool:
        return isinstance(record, dict) and record.get("status", "active") == "active"

    @staticmethod
    def _raise_state_error(state: dict[str, Any]) -> None:
        error = state.get("error")
        if not isinstance(error, dict):
            raise ArtifactGraphError(
                "Artifact relation graph build failed",
                code="ARTIFACT_RELATIONS_BUILD_FAILED",
            )
        raise ArtifactGraphError(
            str(error.get("message") or "Artifact relation graph build failed"),
            code=str(error.get("code") or "ARTIFACT_RELATIONS_BUILD_FAILED"),
            details=error.get("details") if isinstance(error.get("details"), dict) else None,
        )

    @staticmethod
    def _cache_valid(cache: Any, to: str) -> bool:
        return bool(
            isinstance(cache, dict)
            and cache.get("schema_version") == SCHEMA_VERSION
            and cache.get("to") == to.rstrip("/")
            and isinstance(cache.get("target_snapshot_id"), str)
            and isinstance(cache.get("nodes"), list)
            and isinstance(cache.get("edges"), list)
        )

    @classmethod
    def _cache_matches(cls, cache: Any, to: str, head: Any) -> bool:
        return cls._cache_valid(cache, to) and cache.get("target_snapshot_id") == head

    async def _cache_current(
        self,
        cache: Any,
        to: str,
        head: Any,
        ctx: RequestContext,
        *,
        ignore_signature: str | None = None,
    ) -> bool:
        if not self._cache_matches(cache, to, head):
            return False
        sources = await self._read_sources(ctx.account_id, to)
        if not isinstance(sources, dict):
            return False
        if ignore_signature is None:
            _, ignore_signature = await self._ignore_state(self._internal_ctx(ctx))
        return (
            sources.get("schema_version") == SCHEMA_VERSION
            and sources.get("parser_version") == PARSER_VERSION
            and sources.get("ignore_signature") == ignore_signature
            and sources.get("to") == to.rstrip("/")
            and sources.get("target_snapshot_id") == head
        )

    async def _response(
        self,
        graph: dict[str, Any],
        ctx: RequestContext,
        *,
        stale: bool,
        building: bool,
    ) -> dict[str, Any]:
        filtered = await self._filter(graph, ctx)
        return {**filtered, "stale": stale, "building": building}

    def _related_pages(
        self,
        text: str,
        source: str,
        *,
        target_root: str | None = None,
        valid_nodes: set[str] | None = None,
        maximum: int | None = None,
    ) -> tuple[set[str], bool]:
        targets: set[str] = set()
        for token in self._markdown.parse(text):
            for child in token.children or []:
                if child.type == "link_open":
                    target = self._resolve_link(source, child.attrGet("href") or "")
                    if not target:
                        continue
                    if target_root and not (
                        target == target_root.rstrip("/")
                        or target.startswith(f"{target_root.rstrip('/')}/")
                    ):
                        continue
                    if valid_nodes is not None:
                        target = self._valid_target(target, valid_nodes)
                        if not target:
                            continue
                    if target in targets:
                        continue
                    if maximum is not None and len(targets) >= maximum:
                        return targets, True
                    targets.add(target)
        return targets, False

    @classmethod
    def _valid_targets(cls, values: Iterable[Any], nodes: set[str]) -> list[str]:
        result: set[str] = set()
        for value in values:
            if not isinstance(value, str):
                continue
            target = cls._valid_target(value, nodes)
            if target:
                result.add(target)
        return sorted(result)

    @staticmethod
    def _valid_target(target: str, nodes: set[str]) -> str:
        if target in nodes:
            return target
        directory = f"{target.rstrip('/')}/"
        return directory if directory in nodes else ""

    def _add_relation(
        self,
        edges: set[tuple[str, str, str]],
        edge: tuple[str, str, str],
    ) -> None:
        if edge in edges:
            return
        self._check_limit("relations", self._max_relations, len(edges) + 1)
        edges.add(edge)

    async def _ignore_state(
        self, ctx: RequestContext
    ) -> tuple[GitIgnoreSpec | None, str]:
        path = self._fs._gitignore_agfs_path(ctx)
        try:
            raw = await self._fs._async_agfs.read(path)
        except (AGFSNotFoundError, FileNotFoundError):
            raw = b""
        raw_bytes = raw if isinstance(raw, bytes) else str(raw).encode("utf-8")
        text = raw_bytes.decode("utf-8")
        lines = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("!") or "\\" in line:
                raise ArtifactGraphError(
                    "Invalid .ovgitignore",
                    code="ARTIFACT_RELATIONS_BUILD_FAILED",
                )
            lines.append(line)
        spec = GitIgnoreSpec.from_lines(lines) if lines else None
        return spec, hashlib.sha256(raw_bytes).hexdigest()

    @staticmethod
    def _excluded(uri: str, ignore: GitIgnoreSpec | None) -> bool:
        leaf = uri.rsplit("/", 1)[-1]
        if leaf in _DERIVED_NAMES:
            return True
        rel = uri.removeprefix("viking://").lstrip("/")
        return bool(ignore and ignore.match_file(rel))

    @staticmethod
    def _required_directories(to: str, files: Iterable[str]) -> set[str]:
        root = f"{to}/"
        result: set[str] = set()
        for uri in files:
            result.add(root)
            parent = posixpath.dirname(uri)
            while parent and parent != "viking:":
                result.add(f"{parent.rstrip('/')}/")
                if parent == to:
                    break
                parent = posixpath.dirname(parent)
        return result

    @staticmethod
    def _containment_edges(to: str, nodes: set[str]) -> set[tuple[str, str, str]]:
        edges: set[tuple[str, str, str]] = set()
        root = f"{to}/"
        for uri in nodes:
            if uri == root:
                continue
            value = uri.rstrip("/")
            parent = f"{posixpath.dirname(value)}/"
            if parent in nodes:
                edges.add(("contains", parent, uri))
        return edges

    @staticmethod
    def _resolve_link(source: str, href: str) -> str:
        value = unquote(href.strip())
        if not value or value.startswith("#"):
            return ""
        parsed = urlsplit(value)
        if parsed.scheme and parsed.scheme != "viking":
            return ""
        if parsed.scheme == "viking":
            path = f"{parsed.netloc}{parsed.path}".strip("/")
            return f"viking://{posixpath.normpath(path)}"
        path = parsed.path
        if not path:
            return ""
        if path.startswith("/"):
            return f"viking://{posixpath.normpath(path).lstrip('/')}"
        parent = posixpath.dirname(source.removeprefix("viking://"))
        return f"viking://{posixpath.normpath(posixpath.join(parent, path))}"

    async def _filter(
        self, graph: dict[str, Any], ctx: RequestContext
    ) -> dict[str, Any]:
        uris = [node["uri"] for node in graph["nodes"]]
        access = await self._fs._can_access_many(uris, ctx, action=AclAction.READ)
        visible = {uri for uri in uris if access.get(uri, False)}
        return {
            **graph,
            "nodes": [node for node in graph["nodes"] if node["uri"] in visible],
            "edges": [
                edge
                for edge in graph["edges"]
                if edge["from"] in visible and edge["to"] in visible
            ],
        }

    @staticmethod
    def _internal_ctx(ctx: RequestContext) -> RequestContext:
        from dataclasses import replace

        return replace(ctx, bypass_acl=True)

    @staticmethod
    def _check_limit(name: str, maximum: int, actual: int) -> None:
        if actual > maximum:
            raise ArtifactGraphTooLargeError(name, maximum, actual)
