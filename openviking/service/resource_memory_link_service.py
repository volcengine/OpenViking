# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Link resource addition reasons to user memories.

This module keeps resource files immutable: all traceability lives in memory
files' MEMORY_FIELDS metadata.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence
from uuid import uuid4

from openviking.core.namespace import canonical_user_root, context_type_for_uri
from openviking.message import Message
from openviking.message.part import TextPart
from openviking.prompts.manager import render_prompt
from openviking.server.identity import RequestContext
from openviking.session.memory.dataclass import MemoryFile, ResolvedOperations
from openviking.session.memory.extract_loop import ExtractLoop
from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler
from openviking.session.memory.memory_updater import (
    ExtractContext,
    MemoryUpdater,
    MemoryUpdateResult,
)
from openviking.session.memory.session_extract_context_provider import SessionExtractContextProvider
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.storage import VikingDBManager
from openviking.storage.viking_fs import VikingFS, get_viking_fs
from openviking_cli.exceptions import NotFoundError
from openviking_cli.utils import VikingURI, get_logger
from openviking_cli.utils.config import get_openviking_config

if TYPE_CHECKING:
    from openviking.service.session_service import SessionService

logger = get_logger(__name__)

_RESOURCE_REASON_MEMORY_TYPES = ["entities", "events", "preferences"]
_RESOURCE_REASON_COMMIT_TIMEOUT_SECONDS = 1800.0
_RESOURCE_ABSTRACT_MAX_CHARS = 200
_ABSTRACT_NOT_READY_MARKERS = (
    "[.abstract.md is not ready]",
    "[Directory abstract is not ready]",
)


@dataclass
class _MemoryRefMatch:
    memory_uri: str
    memory_file: MemoryFile
    resource_ref: Dict[str, Any]


class _ResourceUnlinkingProvider(SessionExtractContextProvider):
    """Provider for removing resource-derived content from one memory file."""

    def __init__(
        self,
        *,
        memory_uri: str,
        resource_uri: str,
        reason: str,
        memory_file: MemoryFile,
        **kwargs: Any,
    ):
        self.memory_uri = memory_uri
        self.resource_uri = resource_uri
        self.reason = reason
        self.memory_file = memory_file
        messages = [
            Message(
                id="resource-unlinking",
                role="user",
                parts=[
                    TextPart(
                        text=(
                            "Deleted resource URI: "
                            f"{resource_uri}\nOriginal reason: {reason}\n"
                            f"Memory URI: {memory_uri}"
                        )
                    )
                ],
            )
        ]
        super().__init__(messages=messages, **kwargs)

    def instruction(self) -> str:
        return render_prompt(
            "processing.resource_unlinking",
            {
                "output_language": self.get_output_language(),
                "memory_uri": self.memory_uri,
                "resource_uri": self.resource_uri,
                "reason": self.reason,
            },
        )

    async def prefetch(self) -> List[Dict[str, Any]]:
        messages = [
            {
                "role": "user",
                "content": (
                    "## Resource Deletion Cleanup\n"
                    f"Deleted resource URI: {self.resource_uri}\n"
                    f"Original add-resource reason: {self.reason}\n"
                    f"Memory to clean: {self.memory_uri}\n\n"
                    "Use the preloaded memory content below. Output the cleanup operation "
                    "as a single JSON response."
                ),
            }
        ]
        await self._append_structured_read_result(messages, 0, self.memory_uri)
        return messages

    def get_tools(self) -> List[str]:
        return []

    def _build_prefetch_search_query(self) -> str:
        return self.reason

    def get_conversation_text(self) -> str:
        return f"{self.reason}\n{self.resource_uri}\n{self.memory_uri}".strip()


class ResourceMemoryLinkService:
    """Create and clean memory references for resources added with a reason."""

    def __init__(
        self,
        *,
        vikingdb: Optional[VikingDBManager] = None,
        viking_fs: Optional[VikingFS] = None,
        session_service: Optional["SessionService"] = None,
    ):
        self._vikingdb = vikingdb
        self._viking_fs = viking_fs
        self._session_service = session_service
        self._background_tasks: set[asyncio.Task] = set()

    def set_dependencies(
        self,
        *,
        vikingdb: Optional[VikingDBManager],
        viking_fs: VikingFS,
        session_service: Optional["SessionService"] = None,
    ) -> None:
        self._vikingdb = vikingdb
        self._viking_fs = viking_fs
        if session_service is not None:
            self._session_service = session_service

    def _get_viking_fs(self) -> VikingFS:
        return self._viking_fs or get_viking_fs()

    async def on_resource_added(
        self,
        *,
        ctx: RequestContext,
        resource_uri: str,
        reason: str,
        source_name: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Bridge add-resource reason extraction through normal session commit."""
        reason = (reason or "").strip()
        if not reason:
            return {"status": "skipped", "reason": "empty_reason"}
        if not resource_uri:
            return {"status": "skipped", "reason": "empty_resource_uri"}
        if not self._session_service:
            return {"status": "skipped", "reason": "session_service_unavailable"}

        added_at = datetime.now(timezone.utc).isoformat()
        resource_abstract = await self._read_resource_directory_abstract(resource_uri, ctx)
        session_id = f"resource_reason_{uuid4().hex}"
        commit_result: Dict[str, Any] = {}
        task_result: Optional[Dict[str, Any]] = None
        delete_session_now = True
        try:
            session = await self._session_service.create(
                ctx,
                session_id=session_id,
                memory_policy={
                    "self": {"enabled": True},
                    "peer": {"enabled": False},
                    "memory_types": _RESOURCE_REASON_MEMORY_TYPES,
                },
            )
            session.add_messages(
                [
                    {
                        "role": "user",
                        "parts": [
                            TextPart(
                                text=self._build_resource_addition_message(
                                    resource_uri=resource_uri,
                                    reason=reason,
                                    source_name=source_name,
                                    added_at=added_at,
                                    resource_abstract=resource_abstract,
                                )
                            )
                        ],
                        "created_at": added_at,
                    }
                ]
            )
            commit_result = await self._session_service.commit_async(
                session_id,
                ctx,
                keep_recent_count=0,
            )
            task_id = commit_result.get("task_id")
            if task_id:
                try:
                    task_result = await self._wait_for_commit_task(
                        task_id=str(task_id),
                        ctx=ctx,
                        timeout=timeout,
                    )
                except asyncio.TimeoutError:
                    delete_session_now = False
                    self._schedule_session_delete_after_task(
                        session_id=session_id,
                        task_id=str(task_id),
                        ctx=ctx,
                    )
                    raise
            return {
                "status": "success",
                "session_id": session_id,
                "commit_task_id": task_id,
                "archive_uri": commit_result.get("archive_uri"),
                "commit_task": task_result,
            }
        finally:
            if delete_session_now:
                await self._delete_temporary_session(session_id, ctx)

    @staticmethod
    def _build_resource_addition_message(
        *,
        resource_uri: str,
        reason: str,
        source_name: Optional[str],
        added_at: str,
        resource_abstract: str,
    ) -> str:
        return (
            "## Resource Addition\n"
            f"Resource URI: {resource_uri}\n"
            f"Source name: {source_name or 'N/A'}\n"
            f"Added at: {added_at or 'N/A'}\n"
            f"Resource abstract: {resource_abstract or 'N/A'}\n"
            f"User reason: {reason}"
        )

    def _schedule_session_delete_after_task(
        self,
        *,
        session_id: str,
        task_id: str,
        ctx: RequestContext,
    ) -> None:
        task = asyncio.create_task(
            self._delete_session_after_task(session_id=session_id, task_id=task_id, ctx=ctx)
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _delete_session_after_task(
        self,
        *,
        session_id: str,
        task_id: str,
        ctx: RequestContext,
    ) -> None:
        try:
            await self._wait_for_commit_task(
                task_id=task_id,
                ctx=ctx,
                timeout=_RESOURCE_REASON_COMMIT_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            logger.warning(
                "Skipped temporary resource reason session cleanup after task %s: %s",
                task_id,
                exc,
            )
            return
        await self._delete_temporary_session(session_id, ctx)

    async def _delete_temporary_session(self, session_id: str, ctx: RequestContext) -> None:
        if not self._session_service:
            return
        try:
            await self._session_service.delete(session_id, ctx)
        except NotFoundError:
            pass
        except Exception as exc:
            logger.warning("Failed to delete temporary resource reason session: %s", exc)

    async def _wait_for_commit_task(
        self,
        *,
        task_id: str,
        ctx: RequestContext,
        timeout: Optional[float],
    ) -> Dict[str, Any]:
        from openviking.service.task_tracker import get_task_tracker

        async def _poll() -> Dict[str, Any]:
            tracker = get_task_tracker()
            while True:
                task = await tracker.get(
                    task_id,
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                )
                if task is None:
                    raise RuntimeError(f"session commit task not found: {task_id}")
                status = task.status.value if hasattr(task.status, "value") else str(task.status)
                if status == "completed":
                    return task.to_dict()
                if status == "failed":
                    raise RuntimeError(task.error or f"session commit task failed: {task_id}")
                await asyncio.sleep(0.1)

        return await asyncio.wait_for(
            _poll(),
            timeout=timeout or _RESOURCE_REASON_COMMIT_TIMEOUT_SECONDS,
        )

    async def before_resource_delete(
        self,
        *,
        ctx: RequestContext,
        resource_uri: str,
        recursive: bool = False,
    ) -> Dict[str, Any]:
        """Remove references from user memories before deleting a resource."""
        if context_type_for_uri(resource_uri) != "resource":
            return {"status": "skipped", "reason": "not_resource"}

        matches = await self._find_referencing_memories(
            ctx=ctx,
            resource_uri=resource_uri,
            recursive=recursive,
        )
        if not matches:
            return {"status": "no_references", "memory_uris": []}

        cleaned: List[str] = []
        deleted: List[str] = []
        errors: List[str] = []
        grouped = self._group_matches_by_memory(matches)
        for memory_uri, memory_matches in grouped.items():
            first = memory_matches[0]
            reason = str(first.resource_ref.get("reason") or "")
            try:
                cleanup_result = await self._cleanup_memory_reference(
                    ctx=ctx,
                    memory_uri=memory_uri,
                    memory_file=first.memory_file,
                    resource_uri=resource_uri,
                    reason=reason,
                )
                cleaned.extend(cleanup_result.written_uris + cleanup_result.edited_uris)
                deleted.extend(cleanup_result.deleted_uris)
                if memory_uri in cleanup_result.deleted_uris:
                    continue
                if not cleanup_result.has_changes():
                    await self._remove_resource_refs(memory_uri, resource_uri, ctx)
                    cleaned.append(memory_uri)
                await self._assert_resource_unlinked(memory_uri, resource_uri, ctx)
            except NotFoundError:
                deleted.append(memory_uri)
            except Exception as exc:
                errors.append(f"{memory_uri}: {exc}")

        if errors:
            raise RuntimeError(
                "resource memory cleanup failed before deleting resource: " + "; ".join(errors)
            )
        return {
            "status": "success",
            "memory_uris": list(dict.fromkeys(cleaned)),
            "deleted_memory_uris": list(dict.fromkeys(deleted)),
        }

    async def _run_extract_loop(
        self,
        *,
        provider: SessionExtractContextProvider,
        ctx: RequestContext,
        allowed_memory_types: Optional[set[str]] = None,
    ) -> tuple[Optional[ResolvedOperations], ExtractContext, MemoryIsolationHandler]:
        config = get_openviking_config()
        vlm = config.vlm.get_vlm_instance()
        viking_fs = self._get_viking_fs()
        extract_context = provider.get_extract_context()
        isolation_handler = MemoryIsolationHandler(
            ctx,
            extract_context,
            allowed_memory_types=allowed_memory_types,
        )
        provider._isolation_handler = isolation_handler
        orchestrator = ExtractLoop(
            vlm=vlm,
            viking_fs=viking_fs,
            ctx=ctx,
            context_provider=provider,
            isolation_handler=isolation_handler,
        )
        operations, _ = await orchestrator.run()
        return operations, extract_context, isolation_handler

    async def _apply_memory_operations(
        self,
        *,
        provider: SessionExtractContextProvider,
        operations: ResolvedOperations,
        ctx: RequestContext,
        extract_context: ExtractContext,
        isolation_handler: MemoryIsolationHandler,
    ) -> MemoryUpdateResult:
        updater = MemoryUpdater(
            registry=provider._get_registry(),
            vikingdb=self._vikingdb,
        )
        return await updater.apply_operations(
            operations,
            ctx,
            extract_context=extract_context,
            isolation_handler=isolation_handler,
        )

    async def _cleanup_memory_reference(
        self,
        *,
        ctx: RequestContext,
        memory_uri: str,
        memory_file: MemoryFile,
        resource_uri: str,
        reason: str,
    ) -> MemoryUpdateResult:
        memory_type = self._infer_memory_type(memory_uri, memory_file)
        provider = _ResourceUnlinkingProvider(
            memory_uri=memory_uri,
            resource_uri=resource_uri,
            reason=reason,
            memory_file=memory_file,
            ctx=ctx,
            viking_fs=self._get_viking_fs(),
        )
        operations, extract_context, isolation_handler = await self._run_extract_loop(
            provider=provider,
            ctx=ctx,
            allowed_memory_types={memory_type} if memory_type else None,
        )
        if not operations:
            return MemoryUpdateResult()
        result = await self._apply_memory_operations(
            provider=provider,
            operations=operations,
            ctx=ctx,
            extract_context=extract_context,
            isolation_handler=isolation_handler,
        )
        for uri in result.written_uris + result.edited_uris:
            await self._remove_resource_refs(uri, resource_uri, ctx)
            if uri == memory_uri:
                await self._restore_cleanup_metadata(uri, memory_file, ctx)
            if await self._delete_empty_cleanup_memory(uri, ctx):
                self._mark_result_deleted(result, uri)
        return result

    async def _restore_cleanup_metadata(
        self,
        memory_uri: str,
        original_memory_file: MemoryFile,
        ctx: RequestContext,
    ) -> None:
        """Keep resource cleanup from introducing schema metadata."""
        viking_fs = self._get_viking_fs()
        raw = await viking_fs.read_file(memory_uri, ctx=ctx)
        mf = MemoryFileUtils.read(raw, uri=memory_uri)
        original_extra_keys = set((original_memory_file.extra_fields or {}).keys())
        mf.extra_fields = {
            key: value for key, value in mf.extra_fields.items() if key in original_extra_keys
        }
        mf.memory_type = original_memory_file.memory_type
        if not original_memory_file.links:
            mf.links = []
        if not original_memory_file.backlinks:
            mf.backlinks = []
        await viking_fs.write_file(memory_uri, MemoryFileUtils.write(mf), ctx=ctx)

    async def _delete_empty_cleanup_memory(self, memory_uri: str, ctx: RequestContext) -> bool:
        """Delete memory files whose visible content was emptied by resource cleanup."""
        if context_type_for_uri(memory_uri) != "memory":
            return False
        viking_fs = self._get_viking_fs()
        try:
            raw = await viking_fs.read_file(memory_uri, ctx=ctx)
        except (NotFoundError, FileNotFoundError):
            return True
        mf = MemoryFileUtils.read(raw, uri=memory_uri)
        if (mf.content or "").strip():
            return False
        directory_uri = memory_uri.rsplit("/", 1)[0]
        await viking_fs.rm(memory_uri, recursive=False, ctx=ctx)
        await MemoryUpdater.refresh_schema_overview(
            viking_fs=viking_fs,
            directory_uri=directory_uri,
            ctx=ctx,
        )
        logger.info("Deleted empty memory after resource cleanup: %s", memory_uri)
        return True

    @staticmethod
    def _mark_result_deleted(result: MemoryUpdateResult, uri: str) -> None:
        result.written_uris = [item for item in result.written_uris if item != uri]
        result.edited_uris = [item for item in result.edited_uris if item != uri]
        if uri not in result.deleted_uris:
            result.add_deleted(uri)

    async def _remove_resource_refs(
        self,
        memory_uri: str,
        resource_uri: str,
        ctx: RequestContext,
    ) -> None:
        viking_fs = self._get_viking_fs()
        raw = await viking_fs.read_file(memory_uri, ctx=ctx)
        mf = MemoryFileUtils.read(raw, uri=memory_uri)
        refs = [
            ref
            for ref in self._coerce_resource_refs(mf.extra_fields.get("resource_refs"))
            if not self._resource_ref_matches(ref.get("resource_uri"), resource_uri, recursive=True)
        ]
        if refs:
            mf.extra_fields["resource_refs"] = refs
        else:
            mf.extra_fields.pop("resource_refs", None)
        await viking_fs.write_file(memory_uri, MemoryFileUtils.write(mf), ctx=ctx)

    async def _find_referencing_memories(
        self,
        *,
        ctx: RequestContext,
        resource_uri: str,
        recursive: bool,
    ) -> List[_MemoryRefMatch]:
        viking_fs = self._get_viking_fs()
        memory_root = f"{canonical_user_root(ctx)}/memories"
        try:
            entries = await viking_fs.tree(
                memory_root,
                ctx=ctx,
                node_limit=1000000,
                level_limit=None,
            )
        except Exception:
            return []

        matches: List[_MemoryRefMatch] = []
        for entry in entries:
            uri = entry.get("uri", "")
            rel_path = entry.get("rel_path", "")
            if entry.get("isDir") or not uri.endswith(".md"):
                continue
            if rel_path.endswith("/.abstract.md") or rel_path.endswith("/.overview.md"):
                continue
            try:
                raw = await viking_fs.read_file(uri, ctx=ctx)
                mf = MemoryFileUtils.read(raw, uri=uri)
            except Exception:
                continue
            for ref in self._coerce_resource_refs(mf.extra_fields.get("resource_refs")):
                if self._resource_ref_matches(ref.get("resource_uri"), resource_uri, recursive):
                    matches.append(_MemoryRefMatch(uri, mf, ref))
        return matches

    async def _read_resource_directory_abstract(
        self,
        resource_uri: str,
        ctx: RequestContext,
    ) -> str:
        """Best-effort directory abstract lookup for resource-addition readability."""
        viking_fs = self._get_viking_fs()
        for abstract_uri in self._resource_abstract_uri_candidates(resource_uri):
            try:
                abstract = await viking_fs.read_file(abstract_uri, ctx=ctx)
            except Exception:
                continue
            abstract = self._clean_resource_abstract(abstract)
            if abstract:
                return abstract
        return ""

    @classmethod
    def _resource_abstract_uri_candidates(cls, resource_uri: str) -> List[str]:
        normalized = (resource_uri or "").strip().rstrip("/")
        if not normalized:
            return []
        candidates = [f"{normalized}/.abstract.md"]
        parent = cls._parent_uri(normalized)
        if parent:
            candidates.append(f"{parent}/.abstract.md")
        return list(dict.fromkeys(candidates))

    @staticmethod
    def _parent_uri(uri: str) -> str:
        scheme_index = uri.find("://")
        min_slash_index = scheme_index + 3 if scheme_index >= 0 else 0
        slash_index = uri.rfind("/")
        if slash_index <= min_slash_index:
            return ""
        return uri[:slash_index]

    @staticmethod
    def _clean_resource_abstract(abstract: Any) -> str:
        text = " ".join(str(abstract or "").split())
        if not text:
            return ""
        if any(text == marker or text.endswith(marker) for marker in _ABSTRACT_NOT_READY_MARKERS):
            return ""
        if len(text) > _RESOURCE_ABSTRACT_MAX_CHARS:
            return text[: _RESOURCE_ABSTRACT_MAX_CHARS - 3].rstrip() + "..."
        return text

    async def _assert_resource_unlinked(
        self,
        memory_uri: str,
        resource_uri: str,
        ctx: RequestContext,
    ) -> None:
        try:
            raw = await self._get_viking_fs().read_file(memory_uri, ctx=ctx)
        except (NotFoundError, FileNotFoundError) as exc:
            raise NotFoundError(memory_uri, "memory") from exc
        mf = MemoryFileUtils.read(raw, uri=memory_uri)
        if resource_uri in (mf.content or ""):
            raise RuntimeError(f"memory content still contains deleted resource URI: {memory_uri}")
        for ref in self._coerce_resource_refs(mf.extra_fields.get("resource_refs")):
            if self._resource_ref_matches(ref.get("resource_uri"), resource_uri, recursive=True):
                raise RuntimeError(f"memory still contains resource ref: {memory_uri}")

    @staticmethod
    def _coerce_resource_refs(value: Any) -> List[Dict[str, Any]]:
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            return [dict(value)]
        return []

    @staticmethod
    def _group_matches_by_memory(
        matches: Sequence[_MemoryRefMatch],
    ) -> Dict[str, List[_MemoryRefMatch]]:
        grouped: Dict[str, List[_MemoryRefMatch]] = {}
        for match in matches:
            grouped.setdefault(match.memory_uri, []).append(match)
        return grouped

    @staticmethod
    def _resource_ref_matches(
        ref_uri: Any,
        target_uri: str,
        recursive: bool,
    ) -> bool:
        if not isinstance(ref_uri, str) or not ref_uri:
            return False
        normalized_ref = ref_uri.rstrip("/")
        normalized_target = target_uri.rstrip("/")
        if normalized_ref == normalized_target:
            return True
        return recursive and normalized_ref.startswith(normalized_target + "/")

    @staticmethod
    def _infer_memory_type(memory_uri: str, memory_file: MemoryFile) -> str:
        memory_type = (
            memory_file.memory_type
            or memory_file.extra_fields.get("memory_type")
            or ""
        )
        if memory_type:
            return str(memory_type)
        parts = [part for part in VikingURI.normalize(memory_uri).split("/") if part]
        try:
            idx = parts.index("memories")
        except ValueError:
            return ""
        if len(parts) > idx + 1:
            return parts[idx + 1].replace(".md", "")
        return ""
