# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Admin reindex executor."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from openviking.core.context import (
    Context,
    ContextLevel,
    ContextType,
    ResourceContentType,
    Vectorize,
)
from openviking.core.namespace import (
    classify_uri,
    context_type_for_uri,
    owner_space_for_uri,
)
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.service.task_tracker import get_task_tracker
from openviking.session.memory.utils.messages import parse_memory_file_with_fields
from openviking.storage.collection_schemas import TextEmbeddingHandler
from openviking.storage.queuefs.embedding_msg_converter import EmbeddingMsgConverter
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.storage.transaction import LockContext, get_lock_manager
from openviking.storage.viking_fs import get_viking_fs
from openviking.utils.embedding_utils import get_resource_content_type
from openviking.utils.skill_processor import SkillProcessor
from openviking_cli.exceptions import NotFoundError, OpenVikingError
from openviking_cli.utils import VikingURI, get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)

REINDEX_TASK_TYPE = "admin_reindex"

_reindex_executor: "ReindexExecutor | None" = None


def get_reindex_executor() -> "ReindexExecutor":
    global _reindex_executor
    if _reindex_executor is None:
        _reindex_executor = ReindexExecutor()
    return _reindex_executor


@dataclass
class _ReindexCounters:
    scanned_records: int = 0
    rebuilt_records: int = 0
    unsupported_records: int = 0
    failed_records: int = 0
    warnings: list[str] = field(default_factory=list)


class ReindexExecutor:
    """Non-destructive reindex orchestration for admin maintenance flows."""

    SUPPORTED_MODES_BY_TYPE = {
        "global_namespace": {"vectors_only", "semantic_and_vectors"},
        "agent_namespace": {"vectors_only", "semantic_and_vectors"},
        "user_namespace": {"vectors_only", "semantic_and_vectors"},
        "skill_namespace": {"vectors_only", "semantic_and_vectors"},
        "resource": {"vectors_only", "semantic_and_vectors"},
        "skill": {"vectors_only", "semantic_and_vectors"},
        "memory": {"vectors_only", "semantic_and_vectors"},
    }

    async def execute(
        self,
        *,
        uri: str,
        mode: str,
        wait: bool,
        ctx: RequestContext,
    ) -> dict[str, Any]:
        object_type = self._infer_target_type(uri)
        self._validate_mode(object_type, mode)

        tracker = get_task_tracker()
        if wait:
            if tracker.has_running(
                REINDEX_TASK_TYPE,
                uri,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            ):
                raise OpenVikingError(
                    f"URI {uri} already has a reindex in progress",
                    code="CONFLICT",
                    details={"uri": uri},
                )
            return await self._run(
                uri=uri,
                object_type=object_type,
                mode=mode,
                ctx=ctx,
            )

        task = tracker.create_if_no_running(
            REINDEX_TASK_TYPE,
            uri,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
        )
        if task is None:
            raise OpenVikingError(
                f"URI {uri} already has a reindex in progress",
                code="CONFLICT",
                details={"uri": uri},
            )

        asyncio.create_task(
            self._run_tracked(
                task.task_id,
                uri=uri,
                object_type=object_type,
                mode=mode,
                ctx=ctx,
            )
        )
        return {
            "task_id": task.task_id,
            "status": "accepted",
            "uri": uri,
            "object_type": object_type,
            "mode": mode,
        }

    def _infer_target_type(self, uri: str) -> str:
        if not uri.startswith("viking://"):
            raise OpenVikingError(
                f"Unsupported reindex URI: {uri}",
                code="UNSUPPORTED_URI",
                details={"uri": uri},
            )
        classification = classify_uri(uri)
        parts = classification.parts
        if not parts:
            return "global_namespace"
        if parts == ("user",):
            return "user_namespace"
        if parts == ("agent",):
            return "agent_namespace"
        if classification.is_user_namespace_root:
            return "user_namespace"
        if classification.is_agent_namespace_root:
            return "agent_namespace"
        if classification.is_memory:
            return "memory"
        if classification.is_skill_namespace:
            return "skill_namespace"
        if classification.is_skill_root:
            return "skill"
        if classification.is_skill:
            raise OpenVikingError(
                f"Unsupported reindex URI: {uri}",
                code="UNSUPPORTED_URI",
                details={"uri": uri},
            )
        if parts[0] in {"resources", "user", "agent"}:
            return "resource"
        raise OpenVikingError(
            f"Unsupported reindex URI: {uri}",
            code="UNSUPPORTED_URI",
            details={"uri": uri},
        )

    async def _tree_all(
        self,
        viking_fs: Any,
        uri: str,
        *,
        show_all_hidden: bool,
        ctx: RequestContext,
    ) -> list[dict[str, Any]]:
        return await viking_fs.tree(
            uri,
            output="original",
            show_all_hidden=show_all_hidden,
            node_limit=None,
            level_limit=None,
            ctx=ctx,
        )

    async def _refresh_namespace_resource_semantics(
        self,
        *,
        target_root: str,
        directories: list[str],
        files: list[str],
        counters: _ReindexCounters,
        ctx: RequestContext,
    ) -> tuple[list[str], list[str]]:
        prefix = self._child_prefix(target_root)
        semantic_roots = sorted(
            {
                directory_uri
                for directory_uri in directories
                if directory_uri.startswith(prefix)
                and "/" not in directory_uri[len(prefix) :]
                and directory_uri[len(prefix) :]
            }
        )
        filtered_directories = [
            directory_uri
            for directory_uri in directories
            if any(
                directory_uri == root or directory_uri.startswith(root + "/")
                for root in semantic_roots
            )
        ]
        filtered_files = [
            file_uri
            for file_uri in files
            if any(file_uri.startswith(root + "/") for root in semantic_roots)
        ]
        filtered_file_set = set(filtered_files)
        for file_uri in files:
            if file_uri in filtered_file_set:
                continue
            counters.unsupported_records += 1
            counters.warnings.append(
                f"Skipped {file_uri}: namespace semantic_and_vectors only refreshes resource directories"
            )
        for semantic_root in semantic_roots:
            await self._run_semantic_processor(
                uri=semantic_root,
                context_type="resource",
                ctx=ctx,
            )
        return filtered_directories, filtered_files

    @staticmethod
    def _child_prefix(root: str) -> str:
        if root.rstrip("/") == "viking:":
            return "viking://"
        return root.rstrip("/") + "/"

    def _is_resource_entry_for_namespace(self, uri: str, target_root: str) -> bool:
        if not uri.startswith(self._child_prefix(target_root)):
            return False
        classification = classify_uri(uri)
        if classification.is_memory or classification.is_skill:
            return False
        return True

    def _is_global_resource_entry(self, uri: str) -> bool:
        return uri == "viking://resources" or uri.startswith("viking://resources/")

    async def _reindex_skill_namespace(
        self,
        *,
        uri: str,
        mode: str,
        counters: _ReindexCounters,
        ctx: RequestContext,
    ) -> None:
        viking_fs = get_viking_fs()
        try:
            entries = await self._tree_all(viking_fs, uri, show_all_hidden=True, ctx=ctx)
        except Exception as exc:
            raise NotFoundError(uri, "resource") from exc

        skill_roots = []
        for entry in entries:
            entry_uri = entry.get("uri")
            if entry_uri and entry.get("isDir") and classify_uri(entry_uri).is_skill_root:
                skill_roots.append(entry_uri)

        for skill_root in sorted(set(skill_roots)):
            await self._reindex_skill(uri=skill_root, mode=mode, counters=counters, ctx=ctx)

        if not skill_roots:
            counters.unsupported_records += 1
            counters.warnings.append(f"No skill roots found under {uri}")

    def _validate_mode(self, object_type: str, mode: str) -> None:
        supported_modes = self.SUPPORTED_MODES_BY_TYPE[object_type]
        if mode not in supported_modes:
            raise OpenVikingError(
                f"Mode {mode} is not supported for {object_type}",
                code="UNSUPPORTED_MODE",
                details={
                    "mode": mode,
                    "object_type": object_type,
                    "supported_modes": sorted(supported_modes),
                },
            )

    async def _run(
        self,
        *,
        uri: str,
        object_type: str,
        mode: str,
        ctx: RequestContext,
    ) -> dict[str, Any]:
        service = get_service()
        if service.viking_fs is None or service.vikingdb_manager is None:
            raise RuntimeError("OpenVikingService not initialized")

        path = service.viking_fs._uri_to_path(uri, ctx=ctx)
        started_at = time.perf_counter()
        counters = _ReindexCounters()

        async with LockContext(get_lock_manager(), [path], lock_mode="subtree"):
            if object_type == "global_namespace":
                await self._reindex_global_namespace(
                    uri=uri,
                    mode=mode,
                    counters=counters,
                    ctx=ctx,
                )
            elif object_type == "agent_namespace":
                await self._reindex_agent_namespace(
                    uri=uri,
                    mode=mode,
                    counters=counters,
                    ctx=ctx,
                )
            elif object_type == "user_namespace":
                await self._reindex_user_namespace(
                    uri=uri,
                    mode=mode,
                    counters=counters,
                    ctx=ctx,
                )
            elif object_type == "skill_namespace":
                await self._reindex_skill_namespace(
                    uri=uri,
                    mode=mode,
                    counters=counters,
                    ctx=ctx,
                )
            elif object_type == "resource":
                await self._reindex_resource(uri=uri, mode=mode, counters=counters, ctx=ctx)
            elif object_type == "skill":
                await self._reindex_skill(uri=uri, mode=mode, counters=counters, ctx=ctx)
            elif object_type == "memory":
                await self._reindex_memory(uri=uri, mode=mode, counters=counters, ctx=ctx)
            else:
                raise OpenVikingError(
                    f"Unsupported reindex type: {object_type}",
                    code="UNSUPPORTED_URI",
                    details={"uri": uri},
                )

        return {
            "status": "completed",
            "uri": uri,
            "object_type": object_type,
            "mode": mode,
            "scanned_records": counters.scanned_records,
            "rebuilt_records": counters.rebuilt_records,
            "unsupported_records": counters.unsupported_records,
            "failed_records": counters.failed_records,
            "duration_ms": int((time.perf_counter() - started_at) * 1000),
            "warnings": counters.warnings,
        }

    async def _run_tracked(
        self,
        task_id: str,
        *,
        uri: str,
        object_type: str,
        mode: str,
        ctx: RequestContext,
    ) -> None:
        tracker = get_task_tracker()
        tracker.start(task_id, account_id=ctx.account_id, user_id=ctx.user.user_id)
        try:
            result = await self._run(
                uri=uri,
                object_type=object_type,
                mode=mode,
                ctx=ctx,
            )
            tracker.complete(task_id, result, account_id=ctx.account_id, user_id=ctx.user.user_id)
        except Exception as exc:
            tracker.fail(task_id, str(exc), account_id=ctx.account_id, user_id=ctx.user.user_id)

    async def _reindex_resource(
        self,
        *,
        uri: str,
        mode: str,
        counters: _ReindexCounters,
        ctx: RequestContext,
    ) -> None:
        if mode == "semantic_and_vectors":
            await self._run_semantic_processor(uri=uri, context_type="resource", ctx=ctx)
            await self._reindex_resource_vectors(uri=uri, counters=counters, ctx=ctx)
            return
        await self._reindex_resource_vectors(uri=uri, counters=counters, ctx=ctx)

    async def _reindex_skill(
        self,
        *,
        uri: str,
        mode: str,
        counters: _ReindexCounters,
        ctx: RequestContext,
    ) -> None:
        if mode == "semantic_and_vectors":
            await self._regenerate_skill_semantics(uri=uri, ctx=ctx)
        await self._reindex_skill_vectors(uri=uri, counters=counters, ctx=ctx)

    async def _reindex_memory(
        self,
        *,
        uri: str,
        mode: str,
        counters: _ReindexCounters,
        ctx: RequestContext,
    ) -> None:
        if mode == "semantic_and_vectors":
            await self._run_semantic_processor(uri=uri, context_type="memory", ctx=ctx)
            await self._reindex_memory_vectors(uri=uri, counters=counters, ctx=ctx)
            return
        await self._reindex_memory_vectors(uri=uri, counters=counters, ctx=ctx)

    async def _run_semantic_processor(
        self, *, uri: str, context_type: str, ctx: RequestContext
    ) -> None:
        processor = SemanticProcessor()
        msg = SemanticMsg(
            uri=uri,
            context_type=context_type,
            recursive=True,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
            agent_id=ctx.user.agent_id,
            role=ctx.role.value,
            skip_vectorization=True,
        )
        await processor.on_dequeue({"data": msg.to_json()})

    async def _reindex_resource_vectors(
        self,
        *,
        uri: str,
        counters: _ReindexCounters,
        ctx: RequestContext,
    ) -> None:
        viking_fs = get_viking_fs()
        try:
            if not await viking_fs.exists(uri, ctx=ctx):
                raise NotFoundError(uri, "resource")
            stat = await viking_fs.stat(uri, ctx=ctx)
            is_dir = stat.get("isDir", stat.get("is_dir")) if isinstance(stat, dict) else False
            if is_dir:
                entries = await self._tree_all(viking_fs, uri, show_all_hidden=True, ctx=ctx)
            else:
                entries = []
        except Exception as exc:
            raise NotFoundError(uri, "resource") from exc

        if is_dir:
            directories = [uri]
            files: list[str] = []
            for entry in entries:
                entry_uri = entry.get("uri")
                if not entry_uri:
                    continue
                if entry.get("isDir"):
                    directories.append(entry_uri)
                elif not self._is_hidden_meta_file(entry_uri):
                    files.append(entry_uri)
        else:
            directories = []
            files = [uri]

        await self._reindex_resource_vectors_from_entries(
            root_uri=uri,
            directories=directories,
            files=files,
            counters=counters,
            ctx=ctx,
        )

    async def _reindex_resource_vectors_from_entries(
        self,
        *,
        root_uri: str,
        directories: Iterable[str],
        files: Iterable[str],
        counters: _ReindexCounters,
        ctx: RequestContext,
    ) -> None:
        deduped_directories = []
        seen_directories = set()
        for directory_uri in directories:
            if directory_uri and directory_uri not in seen_directories:
                deduped_directories.append(directory_uri)
                seen_directories.add(directory_uri)

        deduped_files = []
        seen_files = set()
        for file_uri in files:
            if file_uri and file_uri not in seen_files:
                deduped_files.append(file_uri)
                seen_files.add(file_uri)

        for directory_uri in deduped_directories:
            if directory_uri == "viking://":
                continue
            counters.scanned_records += 1
            abstract = await self._read_directory_abstract(directory_uri, ctx=ctx)
            overview = await self._read_directory_overview(directory_uri, ctx=ctx)
            if not overview:
                overview = abstract
            if not abstract and not overview:
                counters.unsupported_records += 1
                counters.warnings.append(f"No semantic source found for {directory_uri}")
                continue
            if abstract:
                try:
                    await self._upsert_context(
                        uri=directory_uri,
                        parent_uri=VikingURI(directory_uri).parent.uri,
                        abstract=abstract,
                        vector_text=abstract,
                        is_leaf=False,
                        context_type=context_type_for_uri(directory_uri),
                        level=ContextLevel.ABSTRACT,
                        ctx=ctx,
                    )
                    counters.rebuilt_records += 1
                except Exception as exc:
                    counters.failed_records += 1
                    counters.warnings.append(f"Failed to reindex {directory_uri} L0 vector: {exc}")
            if overview:
                try:
                    await self._upsert_context(
                        uri=directory_uri,
                        parent_uri=VikingURI(directory_uri).parent.uri,
                        abstract=abstract,
                        vector_text=overview,
                        is_leaf=False,
                        context_type=context_type_for_uri(directory_uri),
                        level=ContextLevel.OVERVIEW,
                        ctx=ctx,
                    )
                    counters.rebuilt_records += 1
                except Exception as exc:
                    counters.failed_records += 1
                    counters.warnings.append(f"Failed to reindex {directory_uri} L1 vector: {exc}")

        for file_uri in deduped_files:
            counters.scanned_records += 1
            parent_uri = VikingURI(file_uri).parent.uri
            summary = await self._best_file_summary(file_uri, ctx=ctx)
            vector_text = await self._best_resource_file_vector_text(file_uri, summary, ctx=ctx)
            if not vector_text:
                counters.unsupported_records += 1
                counters.warnings.append(f"No vector source found for {file_uri}")
                continue
            abstract = self._prefer_non_empty(summary, vector_text)
            try:
                await self._upsert_context(
                    uri=file_uri,
                    parent_uri=parent_uri,
                    abstract=abstract,
                    vector_text=vector_text,
                    is_leaf=True,
                    context_type=context_type_for_uri(file_uri),
                    level=ContextLevel.DETAIL,
                    ctx=ctx,
                )
                counters.rebuilt_records += 1
            except Exception as exc:
                counters.failed_records += 1
                counters.warnings.append(f"Failed to reindex {file_uri} vector: {exc}")

    async def _reindex_user_namespace(
        self,
        *,
        uri: str,
        mode: str,
        counters: _ReindexCounters,
        ctx: RequestContext,
    ) -> None:
        normalized_uri = uri.rstrip("/")
        target_root = normalized_uri if normalized_uri else uri
        viking_fs = get_viking_fs()
        try:
            entries = await self._tree_all(viking_fs, target_root, show_all_hidden=True, ctx=ctx)
        except Exception as exc:
            raise NotFoundError(uri, "resource") from exc

        if target_root == "viking://user":
            user_roots = [
                entry.get("uri")
                for entry in entries
                if entry.get("isDir") and classify_uri(entry.get("uri", "")).is_user_namespace_root
            ]
            if user_roots:
                for user_root in sorted(set(user_roots)):
                    await self._reindex_user_namespace(
                        uri=user_root,
                        mode=mode,
                        counters=counters,
                        ctx=ctx,
                    )
                return

        memory_roots: list[str] = []
        resource_directories: list[str] = []
        resource_files: list[str] = []

        for entry in entries:
            entry_uri = entry.get("uri")
            if not entry_uri:
                continue
            classification = classify_uri(entry_uri)
            if classification.is_memory:
                if entry.get("isDir") and classification.is_memory_root:
                    memory_roots.append(entry_uri)
                continue
            if not self._is_resource_entry_for_namespace(entry_uri, target_root):
                continue
            if entry.get("isDir"):
                resource_directories.append(entry_uri)
            elif not self._is_hidden_meta_file(entry_uri):
                resource_files.append(entry_uri)

        for memory_root in sorted(set(memory_roots)):
            memory_mode = (
                "semantic_and_vectors" if mode == "semantic_and_vectors" else "vectors_only"
            )
            await self._reindex_memory(
                uri=memory_root, mode=memory_mode, counters=counters, ctx=ctx
            )

        if mode == "semantic_and_vectors":
            (
                resource_directories,
                resource_files,
            ) = await self._refresh_namespace_resource_semantics(
                target_root=target_root,
                directories=resource_directories,
                files=resource_files,
                counters=counters,
                ctx=ctx,
            )

        await self._reindex_resource_vectors_from_entries(
            root_uri=target_root,
            directories=resource_directories,
            files=resource_files,
            counters=counters,
            ctx=ctx,
        )

    async def _reindex_agent_namespace(
        self,
        *,
        uri: str,
        mode: str,
        counters: _ReindexCounters,
        ctx: RequestContext,
    ) -> None:
        normalized_uri = uri.rstrip("/")
        target_root = normalized_uri if normalized_uri else uri
        viking_fs = get_viking_fs()
        try:
            entries = await self._tree_all(viking_fs, target_root, show_all_hidden=True, ctx=ctx)
        except Exception as exc:
            raise NotFoundError(uri, "resource") from exc

        if target_root == "viking://agent":
            agent_roots = [
                entry.get("uri")
                for entry in entries
                if entry.get("isDir") and classify_uri(entry.get("uri", "")).is_agent_namespace_root
            ]
            if agent_roots:
                for agent_root in sorted(set(agent_roots)):
                    await self._reindex_agent_namespace(
                        uri=agent_root,
                        mode=mode,
                        counters=counters,
                        ctx=ctx,
                    )
                return

        memory_roots: list[str] = []
        skill_roots: list[str] = []
        resource_directories: list[str] = []
        resource_files: list[str] = []

        for entry in entries:
            entry_uri = entry.get("uri")
            if not entry_uri:
                continue
            classification = classify_uri(entry_uri)
            if classification.is_memory:
                if entry.get("isDir") and classification.is_memory_root:
                    memory_roots.append(entry_uri)
                continue
            if classification.is_skill:
                if entry.get("isDir") and classification.is_skill_root:
                    skill_roots.append(entry_uri)
                continue
            if not self._is_resource_entry_for_namespace(entry_uri, target_root):
                continue
            if entry.get("isDir"):
                resource_directories.append(entry_uri)
            elif not self._is_hidden_meta_file(entry_uri):
                resource_files.append(entry_uri)

        for memory_root in sorted(set(memory_roots)):
            memory_mode = (
                "semantic_and_vectors" if mode == "semantic_and_vectors" else "vectors_only"
            )
            await self._reindex_memory(
                uri=memory_root, mode=memory_mode, counters=counters, ctx=ctx
            )

        for skill_root in sorted(set(skill_roots)):
            skill_mode = (
                "semantic_and_vectors" if mode == "semantic_and_vectors" else "vectors_only"
            )
            await self._reindex_skill(uri=skill_root, mode=skill_mode, counters=counters, ctx=ctx)

        if mode == "semantic_and_vectors":
            (
                resource_directories,
                resource_files,
            ) = await self._refresh_namespace_resource_semantics(
                target_root=target_root,
                directories=resource_directories,
                files=resource_files,
                counters=counters,
                ctx=ctx,
            )

        await self._reindex_resource_vectors_from_entries(
            root_uri=target_root,
            directories=resource_directories,
            files=resource_files,
            counters=counters,
            ctx=ctx,
        )

    async def _reindex_global_namespace(
        self,
        *,
        uri: str,
        mode: str,
        counters: _ReindexCounters,
        ctx: RequestContext,
    ) -> None:
        target_root = "viking://"
        viking_fs = get_viking_fs()
        try:
            entries = await self._tree_all(viking_fs, target_root, show_all_hidden=True, ctx=ctx)
        except Exception as exc:
            raise NotFoundError(uri, "resource") from exc

        user_roots: list[str] = []
        agent_roots: list[str] = []
        resource_directories: list[str] = []
        resource_files: list[str] = []

        for entry in entries:
            entry_uri = entry.get("uri")
            if not entry_uri:
                continue
            if entry_uri in {"viking://user", "viking://agent"}:
                continue
            if entry_uri.startswith("viking://user/"):
                remainder = entry_uri[len("viking://user/") :]
                if entry.get("isDir") and remainder and "/" not in remainder:
                    user_roots.append(entry_uri)
                continue
            if entry_uri.startswith("viking://agent/"):
                remainder = entry_uri[len("viking://agent/") :]
                if entry.get("isDir") and remainder and "/" not in remainder:
                    agent_roots.append(entry_uri)
                continue
            if entry_uri == "viking://session" or entry_uri.startswith("viking://session/"):
                continue
            if not self._is_global_resource_entry(entry_uri):
                continue
            if entry.get("isDir"):
                resource_directories.append(entry_uri)
            elif not self._is_hidden_meta_file(entry_uri):
                resource_files.append(entry_uri)

        for user_root in sorted(set(user_roots)):
            await self._reindex_user_namespace(
                uri=user_root,
                mode=mode,
                counters=counters,
                ctx=ctx,
            )

        for agent_root in sorted(set(agent_roots)):
            await self._reindex_agent_namespace(
                uri=agent_root,
                mode=mode,
                counters=counters,
                ctx=ctx,
            )

        if mode == "semantic_and_vectors":
            (
                resource_directories,
                resource_files,
            ) = await self._refresh_namespace_resource_semantics(
                target_root=target_root,
                directories=resource_directories,
                files=resource_files,
                counters=counters,
                ctx=ctx,
            )

        await self._reindex_resource_vectors_from_entries(
            root_uri=target_root,
            directories=resource_directories,
            files=resource_files,
            counters=counters,
            ctx=ctx,
        )

    async def _reindex_skill_vectors(
        self,
        *,
        uri: str,
        counters: _ReindexCounters,
        ctx: RequestContext,
    ) -> None:
        viking_fs = get_viking_fs()
        counters.scanned_records += 1

        abstract = await self._read_directory_abstract(uri, ctx=ctx)
        overview = await self._read_directory_overview(uri, ctx=ctx)
        if not abstract:
            record = await self._fetch_existing_record(uri=uri, level=0, ctx=ctx)
            abstract = self._record_abstract(record)
        if not overview:
            record = await self._fetch_existing_record(uri=uri, level=1, ctx=ctx)
            overview = self._record_abstract(record) or abstract

        if not abstract and not overview:
            counters.unsupported_records += 1
            counters.warnings.append(f"No semantic source found for {uri}")
            return

        parent_uri = VikingURI(uri).parent.uri
        if abstract:
            try:
                await self._upsert_context(
                    uri=uri,
                    parent_uri=parent_uri,
                    abstract=abstract,
                    vector_text=abstract,
                    is_leaf=False,
                    context_type=ContextType.SKILL.value,
                    level=ContextLevel.ABSTRACT,
                    meta=await self._skill_meta(uri=uri, abstract=abstract, ctx=ctx),
                    ctx=ctx,
                )
                counters.rebuilt_records += 1
            except Exception as exc:
                counters.failed_records += 1
                counters.warnings.append(f"Failed to reindex {uri} L0 vector: {exc}")
        if overview:
            try:
                await self._upsert_context(
                    uri=uri,
                    parent_uri=parent_uri,
                    abstract=abstract,
                    vector_text=overview,
                    is_leaf=False,
                    context_type=ContextType.SKILL.value,
                    level=ContextLevel.OVERVIEW,
                    meta=await self._skill_meta(uri=uri, abstract=abstract, ctx=ctx),
                    ctx=ctx,
                )
                counters.rebuilt_records += 1
            except Exception as exc:
                counters.failed_records += 1
                counters.warnings.append(f"Failed to reindex {uri} L1 vector: {exc}")

        skill_file_uri = f"{uri}/SKILL.md"
        if await viking_fs.exists(skill_file_uri, ctx=ctx):
            counters.scanned_records += 1
            skill_content = await self._safe_read_text(skill_file_uri, ctx=ctx)
            if skill_content:
                detail_abstract = self._prefer_non_empty(abstract, skill_content)
                try:
                    await self._upsert_context(
                        uri=skill_file_uri,
                        parent_uri=uri,
                        abstract=detail_abstract,
                        vector_text=skill_content,
                        is_leaf=True,
                        context_type=ContextType.SKILL.value,
                        level=ContextLevel.DETAIL,
                        ctx=ctx,
                    )
                    counters.rebuilt_records += 1
                except Exception as exc:
                    counters.failed_records += 1
                    counters.warnings.append(f"Failed to reindex {skill_file_uri} vector: {exc}")

    async def _reindex_memory_directory_vectors(
        self,
        *,
        uri: str,
        counters: _ReindexCounters,
        ctx: RequestContext,
    ) -> None:
        counters.scanned_records += 1
        abstract = await self._read_directory_abstract(uri, ctx=ctx)
        overview = await self._read_directory_overview(uri, ctx=ctx)
        if not abstract and not overview:
            counters.unsupported_records += 1
            counters.warnings.append(f"No semantic source found for {uri}")
            return

        parent_uri = VikingURI(uri).parent.uri
        if abstract:
            try:
                await self._upsert_context(
                    uri=uri,
                    parent_uri=parent_uri,
                    abstract=abstract,
                    vector_text=abstract,
                    is_leaf=False,
                    context_type=ContextType.MEMORY.value,
                    level=ContextLevel.ABSTRACT,
                    ctx=ctx,
                )
                counters.rebuilt_records += 1
            except Exception as exc:
                counters.failed_records += 1
                counters.warnings.append(f"Failed to reindex {uri} L0 vector: {exc}")
        if overview:
            try:
                await self._upsert_context(
                    uri=uri,
                    parent_uri=parent_uri,
                    abstract=abstract,
                    vector_text=overview,
                    is_leaf=False,
                    context_type=ContextType.MEMORY.value,
                    level=ContextLevel.OVERVIEW,
                    ctx=ctx,
                )
                counters.rebuilt_records += 1
            except Exception as exc:
                counters.failed_records += 1
                counters.warnings.append(f"Failed to reindex {uri} L1 vector: {exc}")

    async def _reindex_memory_vectors(
        self,
        *,
        uri: str,
        counters: _ReindexCounters,
        ctx: RequestContext,
    ) -> None:
        viking_fs = get_viking_fs()
        if await viking_fs.exists(uri, ctx=ctx):
            stat = await viking_fs.stat(uri, ctx=ctx)
            if stat.get("isDir", stat.get("is_dir")):
                entries = await self._tree_all(viking_fs, uri, show_all_hidden=False, ctx=ctx)
                directory_uris = {uri}
                for entry in entries:
                    entry_uri = entry.get("uri")
                    if entry_uri and entry.get("isDir"):
                        directory_uris.add(entry_uri)
                await self._reindex_memory_directory_chain(
                    directory_uris=sorted(directory_uris),
                    counters=counters,
                    ctx=ctx,
                )
                file_uris = [entry["uri"] for entry in entries if not entry.get("isDir")]
            else:
                file_uris = [uri]
        else:
            raise NotFoundError(uri, "memory")

        for file_uri in file_uris:
            counters.scanned_records += 1
            body = await self._safe_read_text(file_uri, ctx=ctx)
            parsed_body = parse_memory_file_with_fields(body) if body else {"content": ""}
            memory_content = parsed_body.get("content", "")
            existing = await self._fetch_existing_record(uri=file_uri, level=2, ctx=ctx)
            abstract = self._best_non_empty(
                self._record_abstract(existing),
                await self._best_file_summary(file_uri, ctx=ctx),
            )
            if not body and existing is None:
                counters.unsupported_records += 1
                counters.warnings.append(f"No memory source found for {file_uri}")
                continue

            parent_uri = VikingURI(file_uri.split("#", 1)[0]).parent.uri
            if body:
                detail_abstract = self._prefer_non_empty(abstract, memory_content, body)
                try:
                    await self._upsert_context(
                        uri=file_uri,
                        parent_uri=parent_uri,
                        abstract=detail_abstract,
                        vector_text=body,
                        is_leaf=True,
                        context_type=ContextType.MEMORY.value,
                        level=ContextLevel.DETAIL,
                        ctx=ctx,
                    )
                    counters.rebuilt_records += 1
                except Exception as exc:
                    counters.failed_records += 1
                    counters.warnings.append(f"Failed to reindex {file_uri} vector: {exc}")
                    continue
                for chunk_uri, chunk_text in self._chunk_memory_body(file_uri, body):
                    counters.scanned_records += 1
                    try:
                        await self._upsert_context(
                            uri=chunk_uri,
                            parent_uri=file_uri,
                            abstract=detail_abstract,
                            vector_text=chunk_text,
                            is_leaf=True,
                            context_type=ContextType.MEMORY.value,
                            level=ContextLevel.DETAIL,
                            ctx=ctx,
                        )
                        counters.rebuilt_records += 1
                    except Exception as exc:
                        counters.failed_records += 1
                        counters.warnings.append(f"Failed to reindex {chunk_uri} vector: {exc}")
                continue

            try:
                await self._upsert_context(
                    uri=file_uri,
                    parent_uri=parent_uri,
                    abstract=abstract,
                    vector_text=abstract,
                    is_leaf=True,
                    context_type=ContextType.MEMORY.value,
                    level=ContextLevel.DETAIL,
                    ctx=ctx,
                )
                counters.rebuilt_records += 1
                counters.warnings.append(
                    f"Reindexed {file_uri} from abstract fallback because original memory body is unavailable"
                )
            except Exception as exc:
                counters.failed_records += 1
                counters.warnings.append(f"Failed to reindex {file_uri} vector: {exc}")

    async def _reindex_memory_directory_chain(
        self,
        *,
        directory_uris: Iterable[str],
        counters: _ReindexCounters,
        ctx: RequestContext,
    ) -> None:
        for directory_uri in directory_uris:
            counters.scanned_records += 1
            abstract = await self._read_directory_abstract(directory_uri, ctx=ctx)
            overview = await self._read_directory_overview(directory_uri, ctx=ctx)
            if not abstract and not overview:
                continue

            parent_uri = VikingURI(directory_uri).parent.uri
            if abstract:
                try:
                    await self._upsert_context(
                        uri=directory_uri,
                        parent_uri=parent_uri,
                        abstract=abstract,
                        vector_text=abstract,
                        is_leaf=False,
                        context_type=ContextType.MEMORY.value,
                        level=ContextLevel.ABSTRACT,
                        ctx=ctx,
                    )
                    counters.rebuilt_records += 1
                except Exception as exc:
                    counters.failed_records += 1
                    counters.warnings.append(f"Failed to reindex {directory_uri} L0 vector: {exc}")
            if overview:
                try:
                    await self._upsert_context(
                        uri=directory_uri,
                        parent_uri=parent_uri,
                        abstract=abstract,
                        vector_text=overview,
                        is_leaf=False,
                        context_type=ContextType.MEMORY.value,
                        level=ContextLevel.OVERVIEW,
                        ctx=ctx,
                    )
                    counters.rebuilt_records += 1
                except Exception as exc:
                    counters.failed_records += 1
                    counters.warnings.append(f"Failed to reindex {directory_uri} L1 vector: {exc}")

    async def _regenerate_skill_semantics(self, *, uri: str, ctx: RequestContext) -> None:
        service = get_service()
        if service.viking_fs is None or service.vikingdb_manager is None:
            raise RuntimeError("OpenVikingService not initialized")

        viking_fs = service.viking_fs
        skill_file_uri = f"{uri}/SKILL.md"
        skill_content = await self._safe_read_text(skill_file_uri, ctx=ctx)
        if not skill_content:
            raise OpenVikingError(
                f"SKILL.md not found for {uri}",
                code="NOT_FOUND",
                details={"uri": uri},
            )

        skill_dict, _, _ = SkillProcessor(service.vikingdb_manager)._parse_skill(
            skill_content,
            allow_local_path_resolution=False,
        )
        overview = await SkillProcessor(service.vikingdb_manager)._generate_overview(
            skill_dict,
            get_openviking_config(),
        )
        await viking_fs.write_context(
            uri=uri,
            content=skill_content,
            abstract=skill_dict.get("description", ""),
            overview=overview,
            content_filename="SKILL.md",
            is_leaf=False,
            ctx=ctx,
        )

    async def _read_directory_abstract(self, uri: str, *, ctx: RequestContext) -> str:
        try:
            return await get_viking_fs().abstract(uri, ctx=ctx)
        except Exception:
            return ""

    async def _read_directory_overview(self, uri: str, *, ctx: RequestContext) -> str:
        try:
            return await get_viking_fs().overview(uri, ctx=ctx)
        except Exception:
            return ""

    async def _best_file_summary(self, uri: str, *, ctx: RequestContext) -> str:
        parent_uri = VikingURI(uri).parent.uri
        file_name = uri.rsplit("/", 1)[-1]
        overviews = await self._safe_read_text(f"{parent_uri}/.overview.md", ctx=ctx)
        if overviews:
            parsed = self._parse_overview_md(overviews)
            if file_name in parsed:
                return parsed[file_name]
        existing = await self._fetch_existing_record(uri=uri, level=2, ctx=ctx)
        return self._record_abstract(existing)

    async def _best_resource_file_vector_text(
        self,
        uri: str,
        summary: str,
        ctx: RequestContext,
    ) -> str:
        text_source = getattr(get_openviking_config().embedding, "text_source", "summary_first")
        existing = await self._fetch_existing_record(uri=uri, level=2, ctx=ctx)
        fallback = self._record_abstract(existing)
        content_type = get_resource_content_type(uri.rsplit("/", 1)[-1])

        if content_type == ResourceContentType.TEXT:
            content = await self._safe_read_text(uri, ctx=ctx)
            if text_source in {"summary_first", "summary_only"} and summary:
                return summary
            if content:
                return self._truncate_embedding_text(content)
            if summary:
                return summary
            return fallback

        if summary:
            return summary
        return fallback

    async def _upsert_context(
        self,
        *,
        uri: str,
        parent_uri: str,
        abstract: str,
        vector_text: str,
        is_leaf: bool,
        context_type: str,
        level: ContextLevel,
        ctx: RequestContext,
        meta: Optional[dict[str, Any]] = None,
    ) -> None:
        service = get_service()
        assert service.vikingdb_manager is not None

        context = Context(
            uri=uri,
            parent_uri=parent_uri,
            is_leaf=is_leaf,
            abstract=abstract or "",
            context_type=context_type,
            level=level,
            user=ctx.user,
            account_id=ctx.account_id,
            owner_space=owner_space_for_uri(uri, ctx),
            meta=meta or {},
        )
        context.set_vectorize(Vectorize(text=vector_text))
        msg = EmbeddingMsgConverter.from_context(context)
        if msg is None:
            raise OpenVikingError(
                f"No vector text generated for {uri}",
                code="FAILED_PRECONDITION",
                details={"uri": uri},
            )

        result = await TextEmbeddingHandler(service.vikingdb_manager).on_dequeue(
            {"data": msg.to_json()}
        )
        if result is None:
            raise OpenVikingError(
                f"Failed to reindex vector for {uri}",
                code="PROCESSING_ERROR",
                details={"uri": uri, "level": int(level)},
            )
        logger.debug("Reindexed vector for %s level=%s", uri, int(level))

    async def _fetch_existing_record(
        self,
        *,
        uri: str,
        level: int,
        ctx: RequestContext,
    ) -> Optional[dict[str, Any]]:
        service = get_service()
        assert service.vikingdb_manager is not None
        records = await service.vikingdb_manager.get_context_by_uri(
            uri=uri,
            level=level,
            limit=1,
            ctx=ctx,
        )
        return records[0] if records else None

    async def _skill_meta(
        self,
        *,
        uri: str,
        abstract: str,
        ctx: RequestContext,
    ) -> dict[str, Any]:
        name = uri.rstrip("/").split("/")[-1]
        return {"name": name, "description": abstract}

    def _record_abstract(self, record: Optional[dict[str, Any]]) -> str:
        if not record:
            return ""
        return str(record.get("abstract") or "")

    def _is_hidden_meta_file(self, uri: str) -> bool:
        return uri.endswith("/.abstract.md") or uri.endswith("/.overview.md")

    def _truncate_embedding_text(self, value: str) -> str:
        max_input_chars = int(
            getattr(get_openviking_config().embedding, "max_input_chars", 1000) or 1000
        )
        if len(value) <= max_input_chars:
            return value
        return value[:max_input_chars] + "\n...(truncated for embedding)"

    async def _safe_read_text(self, uri: str, *, ctx: RequestContext) -> str:
        viking_fs = get_viking_fs()
        try:
            if not await viking_fs.exists(uri, ctx=ctx):
                return ""
            content = await viking_fs.read_file(uri, ctx=ctx)
            if isinstance(content, bytes):
                return content.decode("utf-8", errors="replace")
            return str(content or "")
        except Exception:
            return ""

    def _chunk_memory_body(self, uri: str, body: str) -> Iterable[tuple[str, str]]:
        semantic = get_openviking_config().semantic
        chunk_chars = semantic.memory_chunk_chars
        overlap = semantic.memory_chunk_overlap
        if len(body) <= chunk_chars:
            return []

        chunks: list[str] = []
        start = 0
        while start < len(body):
            end = start + chunk_chars
            if end < len(body):
                boundary = body.rfind("\n\n", start, end)
                if boundary > start + chunk_chars // 2:
                    end = boundary + 2
            chunks.append(body[start:end].strip())
            start = end - overlap
            if start >= len(body):
                break

        return [(f"{uri}#chunk_{idx:04d}", chunk) for idx, chunk in enumerate(chunks) if chunk]

    def _best_non_empty(self, *values: str) -> str:
        for value in values:
            if value:
                return value
        return ""

    def _prefer_non_empty(self, *values: str) -> str:
        for value in values:
            if value:
                return value
        return ""

    @staticmethod
    def _parse_overview_md(content: str) -> dict[str, str]:
        parsed: dict[str, str] = {}
        current_name: Optional[str] = None
        current_lines: list[str] = []
        for line in (content or "").splitlines():
            if line.startswith("## "):
                if current_name is not None:
                    parsed[current_name] = "\n".join(current_lines).strip()
                current_name = line[3:].strip()
                current_lines = []
                continue
            if current_name is not None:
                current_lines.append(line)
        if current_name is not None:
            parsed[current_name] = "\n".join(current_lines).strip()
        return parsed
