# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Grep and search-backend mixin for VikingFS."""

import asyncio
import re
import sys
import time
from typing import Any, Callable, Dict, List, Optional

from openviking.pyagfs.exceptions import AGFSNotSupportedError
from openviking.server.identity import RequestContext
from openviking.storage.expr import And, PathScope, RawDSL
from openviking.storage.viking_fs._base import logger
from openviking_cli.utils.config.grep_config import GrepEngine


def _pkg():
    return sys.modules[__package__]


class _GrepMixin:
    """Grep and search-backend methods."""

    async def grep(
        self,
        uri: str,
        pattern: str,
        exclude_uri: Optional[str] = None,
        case_insensitive: bool = False,
        node_limit: Optional[int] = None,
        level_limit: int = 10,
        ctx: Optional[RequestContext] = None,
        content_transform: Optional[Callable[[str, str], str]] = None,
    ) -> Dict:
        """Content search by pattern or keywords.

        Optimized implementation that uses agfs native grep when possible.
        The ragfs layer greps transparently over encrypted and plaintext files
        (it decrypts via account_id when an encryption layer is configured).
        Falls back to VikingFS layer implementation if native grep is unavailable.
        When engine="auto" and vikingdb is available with sufficient data,
        uses vikingdb bm25 recall + local fs precise matching.

        Args:
            uri: Viking URI
            pattern: Regular expression pattern to search for
            exclude_uri: Optional URI prefix to exclude from search
            case_insensitive: Whether to perform case-insensitive matching
            node_limit: Maximum number of results to return
            level_limit: Maximum depth level to traverse (default: 10)
            ctx: Request context
            content_transform: Optional projection applied before regex matching.
            Internal bm25 recall limit is auto-adapted from node_limit as
            min(node_limit * 5, 100000); when node_limit is unset, use 100000.

        Returns:
            Dict with matches, count, match_count, files_scanned
        """
        self._ensure_access(uri, ctx)
        # Skip vector_store.count() — the count field is not needed for grep,
        # and avoiding it saves one VikingDB API call.
        await self.stat(uri, ctx=ctx, skip_count=True)

        # Read engine and threshold from grep_config (ov.conf)
        engine = self.grep_config.engine if self.grep_config else "auto"
        switch_to_remote_threshold = (
            self.grep_config.switch_to_remote_threshold if self.grep_config else 10000
        )

        # A projection must run before matching. The remote BM25 index contains
        # persisted raw content, so it cannot safely recall projected results.
        resolved_engine = (
            "fs"
            if content_transform is not None
            else await self._resolve_grep_engine(engine, uri, ctx, switch_to_remote_threshold)
        )

        if resolved_engine == "fs":
            return await self._grep_fs(
                uri=uri,
                pattern=pattern,
                exclude_uri=exclude_uri,
                case_insensitive=case_insensitive,
                node_limit=node_limit,
                level_limit=level_limit,
                ctx=ctx,
                content_transform=content_transform,
            )
        else:  # "vikingdb_then_fs"
            return await self._grep_vikingdb_then_fs(
                uri=uri,
                pattern=pattern,
                exclude_uri=exclude_uri,
                case_insensitive=case_insensitive,
                node_limit=node_limit,
                level_limit=level_limit,
                ctx=ctx,
            )

    async def _resolve_grep_engine(
        self, engine: GrepEngine, uri: str, ctx, switch_to_remote_threshold: int = 10000
    ) -> str:
        """Resolve the actual grep engine to use."""
        if engine == "fs":
            return "fs"

        # auto mode: check vikingdb availability
        vector_store = self._get_vector_store()
        if not vector_store:
            return "fs"

        backend_type = getattr(vector_store, "_backend_type", "unknown")
        # Keep this set consistent with ``CollectionAdapter.USE_CONTENT_FIELD``:
        # only these backends store the ``content`` field required for full-text grep.
        if backend_type not in ("volcengine", "vikingdb"):
            return "fs"

        # Check collection has content field and FullText config
        if not await self._collection_has_fulltext(vector_store, ctx):
            return "fs"

        # switch_to_remote_threshold=0 means always use vikingdb
        if switch_to_remote_threshold == 0:
            return "vikingdb_then_fs"

        # Check data volume threshold
        try:
            count = await self._get_cached_count(uri, ctx)
            if count < switch_to_remote_threshold:
                return "fs"
        except Exception:
            logger.debug(
                "grep engine=auto: count() check failed, falling back to fs", exc_info=True
            )
            return "fs"

        return "vikingdb_then_fs"

    async def _collection_has_fulltext(self, vector_store, ctx) -> bool:
        """Check if collection has content field and FullText config.

        Result is cached on the VikingFS instance since collection schema
        does not change at runtime.
        """
        if self._fulltext_available is not None:
            return self._fulltext_available
        try:
            meta = None
            if hasattr(vector_store, "get_collection_meta"):
                meta = await vector_store.get_collection_meta(ctx=ctx)
            if not meta:
                self._fulltext_available = False
                return False
            fields = meta.get("Fields", [])
            has_content = any(
                f.get("FieldName") == "content" and f.get("FieldType") == "text" for f in fields
            )
            fulltext = meta.get("FullText") or []
            has_content_fulltext = any(ft.get("Field") == "content" for ft in fulltext)
            result = has_content and has_content_fulltext
            self._fulltext_available = result
            return result
        except Exception:
            logger.debug(
                "Failed to check collection fulltext config, assuming no fulltext", exc_info=True
            )
            return False

    async def _get_cached_count(self, uri: str, ctx) -> int:
        """Get cached count of records for a URI (TTL=1h)."""
        _COUNT_CACHE_TTL = 3600
        vector_store = self._get_vector_store()

        # Include account_id in cache key for multi-tenant safety
        account_id = getattr(ctx, "account_id", None) if ctx else None
        cache_key = f"{account_id}:{uri}" if account_id else uri

        now = time.time()
        cached = self._count_cache.get(cache_key)
        if cached and (now - cached[1]) < _COUNT_CACHE_TTL:
            return cached[0]

        count = await vector_store.count(filter=PathScope("uri", uri, depth=-1), ctx=ctx)
        # Evict oldest entries if cache exceeds max size
        if len(self._count_cache) >= self._count_cache_max_size:
            oldest_keys = sorted(self._count_cache, key=lambda k: self._count_cache[k][1])
            for k in oldest_keys[: len(oldest_keys) // 2]:
                del self._count_cache[k]
        self._count_cache[cache_key] = (count, now)
        return count

    async def _grep_fs(
        self,
        uri,
        pattern,
        exclude_uri,
        case_insensitive,
        node_limit,
        level_limit,
        ctx,
        content_transform=None,
    ):
        """Filesystem grep path: prefer native agfs grep and fall back if unavailable."""
        if content_transform is None:
            try:
                return await self._grep_with_agfs(
                    uri=uri,
                    pattern=pattern,
                    exclude_uri=exclude_uri,
                    case_insensitive=case_insensitive,
                    node_limit=node_limit,
                    level_limit=level_limit,
                    ctx=ctx,
                )
            except (AttributeError, AGFSNotSupportedError, NotImplementedError) as e:
                logger.debug(f"agfs grep unavailable, falling back to VikingFS implementation: {e}")

        return await self._grep_encrypted(
            uri=uri,
            pattern=pattern,
            exclude_uri=exclude_uri,
            case_insensitive=case_insensitive,
            node_limit=node_limit,
            level_limit=level_limit,
            ctx=ctx,
            content_transform=content_transform,
        )

    async def _grep_vikingdb_then_fs(
        self,
        uri,
        pattern,
        exclude_uri,
        case_insensitive,
        node_limit,
        level_limit,
        ctx,
    ):
        """VikingDB bm25 recall + local fs precise matching."""
        vector_store = self._get_vector_store()

        # Split regex alternation (e.g. "error|warning|fail") and join as a
        # single query string for bm25 search. VikingDB's standard tokenizer
        # will handle the tokenization of the query string.
        query = " ".join(kw.strip() for kw in pattern.split("|") if kw.strip())
        filter_expr = PathScope("uri", uri, depth=level_limit)
        excluded_prefix = None
        if exclude_uri:
            excluded_prefix = exclude_uri.rstrip("/")
            self._ensure_access(excluded_prefix, ctx)
            filter_expr = And([
                filter_expr,
                RawDSL({
                    "op": "must_not",
                    "field": "uri",
                    "conds": [excluded_prefix],
                    "para": "-d=-1",
                }),
            ])

        # Auto-adapt bm25 recall limit: recall up to 5x requested matches
        # while capping at VikingDB's max limit. If node_limit is unset,
        # use the maximum limit to avoid truncation.
        remote_return_limit = min(node_limit * 5, 100000) if node_limit else 100000

        # Step 1: vikingdb recall candidate files
        try:
            logger.debug(
                "grep vikingdb search_by_keywords request: query=%r limit=%s filter=%r "
                "output_fields=%s",
                query,
                remote_return_limit,
                filter_expr,
                ["uri"],
            )
            result = await vector_store.search_by_keywords(
                query=query,
                limit=remote_return_limit,
                filter=filter_expr,
                output_fields=["uri"],
                ctx=ctx,
            )
        except Exception as e:
            logger.warning(f"grep vikingdb step failed, falling back to fs: {e}")
            return await self._grep_fs(
                uri=uri,
                pattern=pattern,
                exclude_uri=exclude_uri,
                case_insensitive=case_insensitive,
                node_limit=node_limit,
                level_limit=level_limit,
                ctx=ctx,
            )

        candidate_uris = [r["uri"] for r in result if r.get("uri")]
        if excluded_prefix:
            candidate_uris = [
                u
                for u in candidate_uris
                if u != excluded_prefix and not u.startswith(excluded_prefix + "/")
            ]
        if not candidate_uris:
            # BM25 returned no candidates — the index confirms no matching content
            return {"matches": [], "count": 0, "match_count": 0, "files_scanned": 0}

        # Step 2: local fs precise matching on candidate files
        return await self._grep_in_files(
            candidate_uris,
            pattern,
            case_insensitive,
            node_limit,
            ctx,
        )

    async def _grep_in_files(
        self,
        file_uris: List[str],
        pattern: str,
        case_insensitive: bool,
        node_limit: Optional[int],
        ctx: Optional[RequestContext],
    ) -> Dict:
        """Execute regex matching in specified file list (vikingdb_then_fs Step 2)."""
        flags = re.IGNORECASE if case_insensitive else 0
        compiled = re.compile(pattern, flags)

        results = []
        files_scanned = 0

        for file_uri in file_uris:
            files_scanned += 1
            try:
                content_bytes = await self.read(file_uri, ctx=ctx)
                content = content_bytes.decode("utf-8", errors="replace")
            except Exception:
                continue

            for line_no, line in enumerate(content.splitlines(), 1):
                if compiled.search(line):
                    results.append({"uri": file_uri, "line": line_no, "content": line})
                    if node_limit and len(results) >= node_limit:
                        return {
                            "matches": results,
                            "count": len(results),
                            "match_count": len(results),
                            "files_scanned": files_scanned,
                        }

        return {
            "matches": results,
            "count": len(results),
            "match_count": len(results),
            "files_scanned": files_scanned,
        }

    async def _grep_with_agfs(
        self,
        uri: str,
        pattern: str,
        exclude_uri: Optional[str] = None,
        case_insensitive: bool = False,
        node_limit: Optional[int] = None,
        level_limit: int = 10,
        ctx: Optional[RequestContext] = None,
    ) -> Dict:
        """Grep using agfs native implementation.

        This is the optimized path for non-encrypted files.
        Uses agfs.grep() which performs matching on the server side.

        Prefer pushing filters down to agfs backend:
        - exclude_uri -> exclude_path
        - level_limit -> level_limit

        Args:
            uri: Viking URI
            pattern: Regular expression pattern to search for
            exclude_uri: Optional URI prefix to exclude from search
            case_insensitive: Whether to perform case-insensitive matching
            node_limit: Maximum number of results to return
            level_limit: Maximum depth level to traverse
            ctx: Request context

        Returns:
            Dict with matches, count, match_count, files_scanned
        """
        path = self._uri_to_path(uri, ctx=ctx)

        excluded_path = None
        if exclude_uri:
            normalized_excluded_uri = exclude_uri.rstrip("/")
            self._ensure_access(normalized_excluded_uri, ctx)
            excluded_path = self._uri_to_path(normalized_excluded_uri, ctx=ctx)

        try:
            result = await self._async_agfs.grep(
                path=path,
                pattern=pattern,
                recursive=True,
                case_insensitive=case_insensitive,
                stream=False,
                node_limit=node_limit,
                exclude_path=excluded_path,
                level_limit=level_limit,
            )
        except (AttributeError, AGFSNotSupportedError, NotImplementedError):
            # Capability missing: let the outer caller fall back to the VikingFS implementation.
            logger.warning("agfs grep unavailable, falling back to VikingFS implementation")
            raise

        matches = result.get("matches", [])
        results = []
        files_scanned_set = set()
        real_ctx = self._ctx_or_default(ctx)

        for match in matches:
            match_file = match.get("file", "")
            if not match_file:
                continue

            agfs_file_path = self._resolve_grep_match_agfs_path(path, match_file)

            file_uri = self._path_to_uri(agfs_file_path, ctx=ctx)
            if not self._is_accessible(file_uri, real_ctx):
                continue

            files_scanned_set.add(file_uri)

            results.append({
                "line": match.get("line", match.get("line_number", 0)),
                "uri": file_uri,
                "content": match.get("content", ""),
            })

            if node_limit and len(results) >= node_limit:
                break

        # Prefer backend-provided scanned file count if available; otherwise fall back to
        # counting files that produced at least one match (best-effort).
        backend_files_scanned = result.get("files_scanned")
        if isinstance(backend_files_scanned, int) and backend_files_scanned >= 0:
            files_scanned = (
                len(files_scanned_set) if real_ctx.actor_peer_id else backend_files_scanned
            )
        else:
            files_scanned = len(files_scanned_set)

        return {
            "matches": results,
            "count": len(results),
            "match_count": len(results),
            "files_scanned": files_scanned,
        }

    async def _grep_encrypted(
        self,
        uri: str,
        pattern: str,
        exclude_uri: Optional[str] = None,
        case_insensitive: bool = False,
        node_limit: Optional[int] = None,
        level_limit: int = 10,
        ctx: Optional[RequestContext] = None,
        content_transform: Optional[Callable[[str, str], str]] = None,
    ) -> Dict:
        """Grep implementation for encrypted files.

        This implementation decrypts files at VikingFS layer before matching.
        Used when encryption is enabled or when agfs.grep is not available.

        Args:
            uri: Viking URI
            pattern: Regular expression pattern to search for
            exclude_uri: Optional URI prefix to exclude from search
            case_insensitive: Whether to perform case-insensitive matching
            node_limit: Maximum number of results to return
            level_limit: Maximum depth level to traverse (default: 10)
            ctx: Request context

        Returns:
            Dict with matches, count, match_count, files_scanned
        """
        flags = re.IGNORECASE if case_insensitive else 0
        compiled_pattern = re.compile(pattern, flags)
        excluded_prefix = None
        if exclude_uri:
            excluded_prefix = exclude_uri.rstrip("/")
            self._ensure_access(excluded_prefix, ctx)
        file_uris = await self._collect_grep_files(
            uri,
            excluded_prefix=excluded_prefix,
            level_limit=level_limit,
            ctx=ctx,
        )
        results, files_scanned = await self._grep_files_parallel(
            file_uris,
            compiled_pattern=compiled_pattern,
            node_limit=node_limit,
            ctx=ctx,
            content_transform=content_transform,
        )

        return {
            "matches": results,
            "count": len(results),
            "match_count": len(results),
            "files_scanned": files_scanned,
        }

    async def _collect_grep_files(
        self,
        uri: str,
        excluded_prefix: Optional[str],
        level_limit: int,
        ctx: Optional[RequestContext] = None,
    ) -> List[str]:
        file_uris: List[str] = []

        async def search_recursive(current_uri: str, current_depth: int) -> None:
            if current_depth > level_limit:
                return

            normalized_current_uri = current_uri
            if excluded_prefix and (
                normalized_current_uri == excluded_prefix
                or normalized_current_uri.startswith(excluded_prefix + "/")
            ):
                logger.debug(f"Skipping excluded uri during grep: {normalized_current_uri}")
                return

            try:
                entries = await self.ls(normalized_current_uri, ctx=ctx)
            except Exception:
                return

            for entry in entries:
                entry_uri = f"{normalized_current_uri.rstrip('/')}/{entry['name']}"
                if excluded_prefix and (
                    entry_uri == excluded_prefix or entry_uri.startswith(excluded_prefix + "/")
                ):
                    logger.debug(f"Skipping excluded uri during grep: {entry_uri}")
                    continue

                if entry.get("isDir"):
                    await search_recursive(entry_uri, current_depth + 1)
                else:
                    file_uris.append(entry_uri)

        normalized_uri = uri
        if excluded_prefix and (
            normalized_uri == excluded_prefix or normalized_uri.startswith(excluded_prefix + "/")
        ):
            logger.debug(f"Skipping excluded uri during grep: {normalized_uri}")
            return file_uris
        try:
            root_stat = await self.stat(normalized_uri, ctx=ctx)
        except Exception:
            return file_uris
        if not root_stat.get("isDir", False):
            file_uris.append(normalized_uri)
            return file_uris

        await search_recursive(uri, 0)
        return file_uris

    async def _grep_files_parallel(
        self,
        file_uris: List[str],
        compiled_pattern: re.Pattern,
        node_limit: Optional[int],
        ctx: Optional[RequestContext] = None,
        content_transform: Optional[Callable[[str, str], str]] = None,
    ) -> tuple[List[Dict[str, Any]], int]:
        results: List[Dict[str, Any]] = []
        files_scanned = 0
        concurrency = _pkg()._DEFAULT_GREP_FILE_CONCURRENCY
        for start in range(0, len(file_uris), concurrency):
            batch_uris = file_uris[start : start + concurrency]
            batch_jobs = [
                self._grep_single_file(
                    entry_uri,
                    compiled_pattern,
                    ctx,
                    content_transform=content_transform,
                )
                for entry_uri in batch_uris
            ]
            batch_results = await asyncio.gather(*batch_jobs)
            for matches, scanned_count in batch_results:
                files_scanned += scanned_count
                for match in matches:
                    results.append(match)
                    if node_limit and len(results) >= node_limit:
                        return results, files_scanned

        return results, files_scanned

    async def _grep_single_file(
        self,
        entry_uri: str,
        compiled_pattern: re.Pattern,
        ctx: Optional[RequestContext] = None,
        content_transform: Optional[Callable[[str, str], str]] = None,
    ) -> tuple[List[Dict[str, Any]], int]:
        try:
            content = await self.read(entry_uri, ctx=ctx)
            if isinstance(content, bytes):
                content = content.decode("utf-8", errors="replace")
            if content_transform is not None:
                content = content_transform(content, entry_uri)

            matches: List[Dict[str, Any]] = []
            lines = content.split("\n")
            for line_num, line in enumerate(lines, 1):
                if compiled_pattern.search(line):
                    matches.append({
                        "line": line_num,
                        "uri": entry_uri,
                        "content": line,
                    })
            return matches, 1
        except Exception as e:
            logger.debug(f"Failed to grep {entry_uri}: {e}")
            return [], 1

    def _resolve_grep_match_agfs_path(self, base_path: str, match_file: str) -> str:
        """Resolve a grep match path (relative to query root) into a full AGFS path."""
        if match_file == ".":
            return base_path
        return f"{base_path.rstrip('/')}/{match_file.lstrip('/')}"
