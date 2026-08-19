# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Semantic retrieval mixin for VikingFS."""

import asyncio
from typing import Any, Dict, List, Optional, Union

from openviking.core.context import ContextLevel
from openviking.core.retrieval_targets import resolve_retrieval_targets
from openviking.server.error_mapping import is_not_found_error, map_exception
from openviking.server.identity import RequestContext
from openviking.storage.semantic_sidecar import body_for_preview, render_semantic_sidecar
from openviking.storage.viking_fs._base import (
    _ensure_non_empty_search_query,
    logger,
)
from openviking.telemetry import get_current_telemetry
from openviking.utils.image_search import build_multimodal_embedding_input
from openviking_cli.exceptions import NotFoundError


class _SemanticMixin:
    """Abstract/overview/find/search semantic retrieval layer."""

    # ========== VikingFS Specific Capabilities ==========

    async def _read_abstract_file(
        self,
        path: str,
        uri: str,
        ctx: Optional[RequestContext] = None,
    ) -> str:
        """Read and decrypt/decode .abstract.md from a known directory path.

        Does NOT perform stat or isDir check -- caller is responsible for
        ensuring the path points to a directory.
        """
        file_path = f"{path}/.abstract.md"
        try:
            content_bytes = self._handle_agfs_read(await self._async_agfs.read(file_path))
        except Exception as exc:
            if not is_not_found_error(exc):
                mapped = map_exception(exc, resource=uri)
                if mapped is not None:
                    raise mapped from exc
                raise
            return f"# {uri} [Directory abstract is not ready]"

        return body_for_preview(self._decode_bytes(content_bytes))

    async def _read_abstract_for_known_dir(
        self,
        uri: str,
        ctx: Optional[RequestContext] = None,
    ) -> str:
        """Read .abstract.md for a directory that is already known to be a directory.

        Bypasses stat() and isDir check. Caller (i.e. _batch_fetch_abstracts)
        must guarantee that the URI points to a directory.
        """
        self._ensure_access(uri, ctx)
        real_ctx = self._ctx_or_default(ctx)
        primary_path = self._uri_to_path(uri, ctx=ctx)
        for path in self._read_paths(uri, ctx=ctx):
            if not await self._read_path_visible(uri, path, primary_path, real_ctx):
                continue
            try:
                if not await self._agfs_path_exists(path):
                    continue
                return await self._read_abstract_file(path, uri, ctx=ctx)
            except Exception as exc:
                if is_not_found_error(exc):
                    continue
                raise
        return f"# {uri} [Directory abstract is not ready]"

    async def abstract(
        self,
        uri: str,
        ctx: Optional[RequestContext] = None,
    ) -> str:
        """Read directory's L0 summary (.abstract.md).

        If the caller points to a file, its parent directory is used instead so
        the endpoint remains usable for both file and directory URIs.
        """
        self._ensure_access(uri, ctx)
        real_ctx = self._ctx_or_default(ctx)
        primary_path = self._uri_to_path(uri, ctx=ctx)
        path = primary_path
        last_exc: Optional[Exception] = None
        for candidate_path in self._read_paths(uri, ctx=ctx):
            if not await self._read_path_visible(uri, candidate_path, primary_path, real_ctx):
                continue
            try:
                info = await self._async_agfs.stat(candidate_path)
                path = candidate_path
                break
            except Exception as exc:
                if is_not_found_error(exc):
                    last_exc = exc
                    continue
                mapped = map_exception(exc, resource=uri)
                if mapped is not None:
                    raise mapped from exc
                raise
        else:
            if last_exc is not None:
                mapped = map_exception(last_exc, resource=uri)
                if mapped is not None:
                    raise mapped from last_exc
            raise NotFoundError(uri, "directory") from last_exc
        if not info.get("isDir", info.get("is_dir")):
            parent_path = path.rsplit("/", 1)[0] or "/"
            parent_uri = self._path_to_uri(parent_path, ctx=ctx)
            logger.info(
                "content/abstract: %s is a file, falling back to parent directory %s",
                uri,
                parent_uri,
            )
            return await self.abstract(parent_uri, ctx=ctx)
        return await self._read_abstract_file(path, uri, ctx=ctx)

    async def overview(
        self,
        uri: str,
        ctx: Optional[RequestContext] = None,
    ) -> str:
        """Read directory's L1 overview (.overview.md).

        If the caller points to a file, its parent directory is used instead so
        the endpoint remains usable for both file and directory URIs.
        """
        self._ensure_access(uri, ctx)
        real_ctx = self._ctx_or_default(ctx)
        primary_path = self._uri_to_path(uri, ctx=ctx)
        path = primary_path
        last_exc: Optional[Exception] = None
        for candidate_path in self._read_paths(uri, ctx=ctx):
            if not await self._read_path_visible(uri, candidate_path, primary_path, real_ctx):
                continue
            try:
                info = await self._async_agfs.stat(candidate_path)
                path = candidate_path
                break
            except Exception as exc:
                if is_not_found_error(exc):
                    last_exc = exc
                    continue
                mapped = map_exception(exc, resource=uri)
                if mapped is not None:
                    raise mapped from exc
                raise
        else:
            if last_exc is not None:
                mapped = map_exception(last_exc, resource=uri)
                if mapped is not None:
                    raise mapped from last_exc
            raise NotFoundError(uri, "directory") from last_exc
        if not info.get("isDir", info.get("is_dir")):
            parent_path = path.rsplit("/", 1)[0] or "/"
            parent_uri = self._path_to_uri(parent_path, ctx=ctx)
            logger.info(
                "content/overview: %s is a file, falling back to parent directory %s",
                uri,
                parent_uri,
            )
            return await self.overview(parent_uri, ctx=ctx)
        file_path = f"{path}/.overview.md"
        try:
            content_bytes = self._handle_agfs_read(await self._async_agfs.read(file_path))
        except Exception as exc:
            if not is_not_found_error(exc):
                mapped = map_exception(exc, resource=uri)
                if mapped is not None:
                    raise mapped from exc
                raise
            # Fallback to default if .overview.md doesn't exist
            return f"# {uri}\n\n[Directory overview is not ready]"

        return body_for_preview(self._decode_bytes(content_bytes))

    async def find(
        self,
        query: str,
        target_uri: Union[str, List[str]] = "",
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict] = None,
        ctx: Optional[RequestContext] = None,
        level: Optional[List[int]] = None,
        image_url: Optional[str] = None,
    ):
        """Semantic search.

        Args:
            query: Search query
            target_uri: Target directory URI(s), supports str or List[str]
            limit: Return count
            score_threshold: Score threshold
            filter: Metadata filter

        Returns:
            FindResult
        """
        _ensure_non_empty_search_query(query, image_url)
        telemetry = get_current_telemetry()
        from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever
        from openviking_cli.retrieve import (
            ContextType,
            FindResult,
            TypedQuery,
        )

        real_ctx = self._ctx_or_default(ctx)
        retrieval_targets = resolve_retrieval_targets(target_uri, real_ctx)

        for target_dir in retrieval_targets.target_directories:
            self._ensure_access(target_dir, ctx)

        storage = self._get_vector_store()
        if not storage:
            raise RuntimeError("Vector store not initialized. Call OpenViking.initialize() first.")

        embedder = self._get_embedder()
        if not embedder:
            raise RuntimeError("Embedder not configured.")

        retriever = HierarchicalRetriever(
            storage=storage,
            embedder=embedder,
            rerank_config=self.rerank_config,
            retrieval_config=self.retrieval_config,
        )

        typed_query = TypedQuery(
            query=query,
            context_type=None,
            intent="",
            target_directories=retrieval_targets.target_directories,
            embedding_input=(
                build_multimodal_embedding_input(query, image_url) if image_url else None
            ),
            image_query=bool(image_url),
        )

        logger.debug(
            "[VikingFS.find] Calling retriever.retrieve with "
            f"ctx.account_id={real_ctx.account_id}, ctx.user={real_ctx.user}"
        )

        result = await retriever.retrieve(
            typed_query,
            ctx=real_ctx,
            limit=limit,
            score_threshold=score_threshold,
            scope_dsl=filter,
            level=level,
        )

        # Convert QueryResult to FindResult
        memories, resources, skills = [], [], []
        for ctx in result.matched_contexts:
            if ctx.context_type == ContextType.MEMORY:
                memories.append(ctx)
            elif ctx.context_type == ContextType.RESOURCE:
                resources.append(ctx)
            elif ctx.context_type == ContextType.SKILL:
                skills.append(ctx)

        find_result = FindResult(
            memories=memories,
            resources=resources,
            skills=skills,
        )
        telemetry.set("vector.returned", find_result.total)
        return find_result

    async def search(
        self,
        query: str,
        target_uri: Union[str, List[str]] = "",
        session_info: Optional[Dict] = None,
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict] = None,
        ctx: Optional[RequestContext] = None,
        level: Optional[List[int]] = None,
        image_url: Optional[str] = None,
    ):
        """Complex search with session context.

        Args:
            query: Search query
            target_uri: Target directory URI(s), supports str or List[str]
            session_info: Session information
            limit: Return count
            filter: Metadata filter

        Returns:
            FindResult
        """
        _ensure_non_empty_search_query(query, image_url)
        telemetry = get_current_telemetry()
        from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever
        from openviking.retrieve.intent_analyzer import IntentAnalyzer
        from openviking_cli.retrieve import (
            ContextType,
            FindResult,
            QueryPlan,
            TypedQuery,
        )

        real_ctx = self._ctx_or_default(ctx)
        retrieval_targets = resolve_retrieval_targets(target_uri, real_ctx)
        primary_target_uri = retrieval_targets.first_explicit_directory

        session_summary = (
            str(session_info.get("latest_archive_overview") or "") if session_info else ""
        )
        current_messages = session_info.get("current_messages") if session_info else None

        query_plan: Optional[QueryPlan] = None
        for target_dir in retrieval_targets.target_directories:
            self._ensure_access(target_dir, ctx)

        # When target_uri exists, read its abstract as optional query-planning context.
        target_abstract = ""
        if primary_target_uri:
            try:
                with telemetry.measure("search.target_abstract"):
                    target_abstract = await self.abstract(primary_target_uri, ctx=ctx)
            except Exception:
                target_abstract = ""

        intent_enabled = (
            bool(self.retrieval_config.enable_intent) if self.retrieval_config is not None else True
        )

        # With session context: optional intent analysis
        if image_url:
            typed_queries = [
                TypedQuery(
                    query=query,
                    context_type=None,
                    intent="",
                    priority=1,
                    target_directories=retrieval_targets.target_directories,
                    embedding_input=build_multimodal_embedding_input(query, image_url),
                    image_query=True,
                )
            ]
        elif intent_enabled and (session_summary or current_messages):
            analyzer = IntentAnalyzer(max_recent_messages=5)
            with telemetry.measure("search.intent_analysis"):
                query_plan = await analyzer.analyze(
                    compression_summary=session_summary or "",
                    messages=current_messages or [],
                    current_message=query,
                    target_abstract=target_abstract,
                )
            typed_queries = query_plan.queries
            for tq in typed_queries:
                tq.target_directories = retrieval_targets.target_directories
        else:
            # No session context, or intent disabled: search with the raw query.
            typed_queries = [
                TypedQuery(
                    query=query,
                    context_type=None,
                    intent="",
                    priority=1,
                    target_directories=retrieval_targets.target_directories,
                )
            ]
        telemetry.set("search.typed_queries_count", len(typed_queries))

        # Concurrent execution
        storage = self._get_vector_store()
        embedder = self._get_embedder()
        retriever = HierarchicalRetriever(
            storage=storage,
            embedder=embedder,
            rerank_config=self.rerank_config,
            retrieval_config=self.retrieval_config,
        )

        async def _execute(tq: TypedQuery):
            real_ctx = self._ctx_or_default(ctx)
            logger.debug(
                "[VikingFS.search._execute] Calling retriever.retrieve with "
                f"ctx.account_id={real_ctx.account_id}, ctx.user={real_ctx.user}"
            )
            return await retriever.retrieve(
                tq,
                ctx=real_ctx,
                limit=limit,
                score_threshold=score_threshold,
                scope_dsl=filter,
                level=level,
            )

        query_results = await asyncio.gather(*[_execute(tq) for tq in typed_queries])

        # Aggregate results to FindResult
        memories, resources, skills = [], [], []
        for result in query_results:
            for ctx in result.matched_contexts:
                if ctx.context_type == ContextType.MEMORY:
                    memories.append(ctx)
                elif ctx.context_type == ContextType.RESOURCE:
                    resources.append(ctx)
                elif ctx.context_type == ContextType.SKILL:
                    skills.append(ctx)

        find_result = FindResult(
            memories=memories,
            resources=resources,
            skills=skills,
            query_plan=query_plan,
            query_results=query_results,
        )
        telemetry.set("vector.returned", find_result.total)
        return find_result

    async def write_context(
        self,
        uri: str,
        content: Union[str, bytes] = "",
        abstract: str = "",
        overview: str = "",
        content_filename: str = "content.md",
        is_leaf: bool = False,
        ctx: Optional[RequestContext] = None,
    ) -> None:
        """Write context to AGFS (L0/L1/L2)."""

        self._ensure_mutable_access(uri, ctx)
        path = self._uri_to_path(uri, ctx=ctx)

        try:
            await self._ensure_parent_dirs(path, ctx=ctx)
            try:
                await self._async_agfs.mkdir(path)
            except Exception as e:
                if "exist" not in str(e).lower():
                    raise

            if content:
                content_uri = f"{uri}/{content_filename}"
                await self.write_file(content_uri, content, ctx=ctx)

            if abstract:
                abstract_uri = f"{uri}/.abstract.md"
                await self.write_file(
                    abstract_uri,
                    render_semantic_sidecar(
                        ContextLevel.ABSTRACT,
                        uri,
                        abstract,
                        {
                            "generated_by": {
                                "component": "VikingFS.write_context",
                                "trigger": "context_write",
                            }
                        },
                    ),
                    ctx=ctx,
                )

            if overview:
                overview_uri = f"{uri}/.overview.md"
                await self.write_file(
                    overview_uri,
                    render_semantic_sidecar(
                        ContextLevel.OVERVIEW,
                        uri,
                        overview,
                        {
                            "generated_by": {
                                "component": "VikingFS.write_context",
                                "trigger": "context_write",
                            }
                        },
                    ),
                    ctx=ctx,
                )

        except Exception as e:
            logger.error(f"[VikingFS] Failed to write {uri}: {e}")
            raise IOError(f"Failed to write {uri}: {e}")
