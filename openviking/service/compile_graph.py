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

from openviking.pyagfs.exceptions import AGFSNotFoundError
from openviking.server.identity import RequestContext, Role
from openviking.storage.acl import AclAction
from openviking_cli.exceptions import OpenVikingError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

SCHEMA_VERSION = 1
PARSER_VERSION = 1
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
        compile_wait_seconds: float = 60.0,
        query_wait_seconds: float = 10.0,
    ) -> None:
        self._store = store
        self._fs = viking_fs
        self._max_nodes = max_nodes
        self._max_markdown_files = max_markdown_files
        self._max_markdown_bytes = max_markdown_bytes
        self._compile_wait_seconds = compile_wait_seconds
        self._query_wait_seconds = query_wait_seconds
        self._markdown = MarkdownIt("commonmark")
        self._compile_pages: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = {}
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

        cached = await self._read_cache(ctx.account_id, record["to"])
        if await self._cache_current(cached, record["to"], wiki_head, ctx):
            return await self._response(cached, ctx, stale=False, building=False)

        await self.enqueue_wakeup(ctx.account_id, record["to"], ctx.user.user_id)
        deadline = time.monotonic() + self._query_wait_seconds
        while time.monotonic() < deadline:
            await asyncio.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
            latest = await self._store.get(ctx.account_id, record["to"])
            latest_head = (latest or record).get("wiki_head")
            if latest_head and latest_head != wiki_head:
                wiki_head = latest_head
                await self.enqueue_wakeup(
                    ctx.account_id,
                    record["to"],
                    ctx.user.user_id,
                )
            current = await self._read_cache(ctx.account_id, record["to"])
            if await self._cache_current(current, record["to"], wiki_head, ctx):
                return await self._response(current, ctx, stale=False, building=False)
            if self._cache_valid(current, record["to"]):
                cached = current

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
    ) -> dict[str, Any]:
        """Reconcile one target to its latest immutable Snapshot."""
        async with self._store.lock_artifact_graph(ctx.account_id, record["to"]):
            current = await self._store.get(ctx.account_id, record["to"])
            head = (current or record).get("wiki_head") or wiki_head
            return await self._reconcile_once(
                record["to"],
                head,
                self._internal_ctx(ctx),
                parsed_pages=parsed_pages,
            )

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
        snapshot: str,
        uri: str,
        ctx: RequestContext,
        content: bytes | None = None,
    ) -> None:
        """Keep a validated-by-OID candidate in memory for Compile finalization."""
        if not uri.lower().endswith(".md") or self._excluded(uri, None):
            return
        if content is None:
            blob = await self._fs.show_blob_raw(snapshot, path=uri, ctx=ctx)
            content = blob["bytes"]
            blob_oid = blob["oid"]
            size = int(blob.get("size") or len(content))
        else:
            blob_oid = hashlib.sha1(
                f"blob {len(content)}\0".encode("ascii") + content,
                usedforsecurity=False,
            ).hexdigest()
            size = len(content)
        text = content.decode("utf-8", errors="replace")
        self._compile_pages.setdefault((account_id, to, operation_id), {})[uri] = {
            "blob_oid": blob_oid,
            "snapshot_id": snapshot,
            "size": size,
            "related_pages": sorted(self._related_pages(text, uri)),
        }

    def discard_compile_pages(self, account_id: str, to: str, operation_id: str) -> None:
        self._compile_pages.pop((account_id, to, operation_id), None)

    def _finish_background(self, task: asyncio.Task[Any]) -> None:
        self._background.discard(task)
        if not task.cancelled():
            task.exception()

    async def reconcile_target(self, account_id: str, to: str, user_id: str) -> None:
        """Worker entrypoint; chase wiki_head until the target is current."""
        ctx = RequestContext(
            user=UserIdentifier(account_id=account_id, user_id=user_id or "artifact-graph"),
            role=Role.USER,
            bypass_acl=True,
        )
        while True:
            record = await self._store.get(account_id, to)
            if record is None or not record.get("initialized") or not record.get("wiki_head"):
                return
            await self.reconcile_graph(record, ctx, wiki_head=record["wiki_head"])
            latest = await self._store.get(account_id, to)
            cached = await self._read_cache(account_id, to)
            if latest is None or await self._cache_current(
                cached,
                to,
                latest.get("wiki_head"),
                ctx,
            ):
                return

    async def enqueue_wakeup(self, account_id: str, to: str, user_id: str) -> bool:
        try:
            from openviking.storage.queuefs import get_queue_manager
            from openviking.storage.queuefs.queue_manager import QueueManager

            await get_queue_manager().enqueue(
                QueueManager.ARTIFACT_RELATIONS,
                {"account_id": account_id, "to": to, "user_id": user_id},
            )
            return True
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to enqueue artifact relation graph for %s", to)
            return False

    async def _reconcile_once(
        self,
        to: str,
        head: str,
        ctx: RequestContext,
        *,
        parsed_pages: dict[str, dict[str, Any]] | None,
    ) -> dict[str, Any]:
        to = to.rstrip("/")
        graph = await self._read_cache(ctx.account_id, to)
        sources = await self._read_sources(ctx.account_id, to)
        ignore, ignore_signature = await self._ignore_state(ctx)
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

        changes = await self._fs.diff_paths(dirs=[to], base=base, head=head, ctx=ctx)
        files = (
            {node["uri"] for node in graph["nodes"] if not node["uri"].endswith("/")}
            if base
            else set()
        )
        pages = dict(sources["pages"]) if base else {}
        page_sizes = dict(sources["page_sizes"]) if base else {}
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
            else:
                pages.pop(uri, None)
                page_sizes.pop(uri, None)

        changed_bytes = 0
        for uri in changed_markdown:
            candidate = (parsed_pages or {}).get(uri)
            candidate_valid = False
            if isinstance(candidate, dict) and candidate.get("snapshot_id"):
                later_changes = await self._fs.diff_paths(
                    dirs=[uri],
                    base=candidate["snapshot_id"],
                    head=head,
                    ctx=ctx,
                )
                candidate_valid = not any(change["uri"] == uri for change in later_changes)
            if candidate_valid:
                related_pages = candidate.get("related_pages")
                if not isinstance(related_pages, list):
                    candidate_valid = False
            if candidate_valid:
                blob_oid = candidate["blob_oid"]
                page_size = int(candidate.get("size") or 0)
            else:
                blob = await self._fs.show_blob_raw(head, path=uri, ctx=ctx)
                blob_oid = blob["oid"]
                page_size = int(blob.get("size") or len(blob["bytes"]))
                text = blob["bytes"].decode("utf-8", errors="replace")
                related_pages = sorted(self._related_pages(text, uri))
            changed_bytes += page_size
            self._check_limit("markdown_bytes", self._max_markdown_bytes, changed_bytes)
            pages[uri] = {
                "blob_oid": blob_oid,
                "related_pages": list(related_pages),
            }
            page_sizes[uri] = page_size

        self._check_limit("markdown_files", self._max_markdown_files, len(pages))
        self._check_limit(
            "markdown_bytes",
            self._max_markdown_bytes,
            sum(int(size) for size in page_sizes.values()),
        )
        nodes = files | self._required_directories(to, files)
        self._check_limit("nodes", self._max_nodes, len(nodes))
        edges = self._containment_edges(to, nodes)
        for source, page in pages.items():
            for target in page["related_pages"]:
                normalized = target if target in nodes else f"{target.rstrip('/')}/"
                if normalized in nodes:
                    edges.add(("references", source, normalized))
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
    ) -> bool:
        if not self._cache_matches(cache, to, head):
            return False
        sources = await self._read_sources(ctx.account_id, to)
        if not isinstance(sources, dict):
            return False
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

    def _related_pages(self, text: str, source: str) -> set[str]:
        targets: set[str] = set()
        for token in self._markdown.parse(text):
            for child in token.children or []:
                if child.type == "link_open":
                    target = self._resolve_link(source, child.attrGet("href") or "")
                    if target:
                        targets.add(target)
        return targets

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
