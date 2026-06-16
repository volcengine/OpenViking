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

from openviking.core.namespace import (
    NamespaceShapeError,
    canonical_user_root,
    canonicalize_uri,
    context_type_for_uri,
    uri_parts,
)
from openviking.core.peer_id import normalize_peer_id
from openviking.message.part import TextPart
from openviking.server.identity import RequestContext
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.memory_updater import (
    MemoryUpdater,
    MemoryUpdateResult,
)
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.session.memory.utils.resource_refs import (
    content_references_resource,
    extract_resource_uris,
    remove_resource_references_from_memory,
    resource_ref_matches,
)
from openviking.storage import VikingDBManager
from openviking.storage.viking_fs import VikingFS, get_viking_fs
from openviking_cli.exceptions import NotFoundError
from openviking_cli.utils import get_logger

if TYPE_CHECKING:
    from openviking.service.session_service import SessionService

logger = get_logger(__name__)

_RESOURCE_REASON_SESSION_ID = "__openviking_resource_reason__"
_RESOURCE_REASON_MEMORY_TYPES = ["entities", "events", "preferences"]
_RESOURCE_REASON_COMMIT_TIMEOUT_SECONDS = 1800.0
_RESOURCE_ABSTRACT_MAX_CHARS = 200
_ABSTRACT_NOT_READY_MARKERS = (
    "[.abstract.md is not ready]",
    "[Directory abstract is not ready]",
)


def _resource_reason_memory_policy(target_peer_id: Optional[str] = None) -> Dict[str, Any]:
    peer_targeted = bool(target_peer_id)
    return {
        "self": {"enabled": not peer_targeted},
        "peer": {"enabled": peer_targeted},
        "memory_types": list(_RESOURCE_REASON_MEMORY_TYPES),
    }


def _resource_reason_peer_id(ctx: RequestContext, resource_uri: str) -> Optional[str]:
    actor_peer_id = normalize_peer_id(ctx.actor_peer_id)
    if actor_peer_id:
        return actor_peer_id
    return _peer_id_from_resource_uri(resource_uri, ctx)


def _peer_id_from_resource_uri(resource_uri: str, ctx: RequestContext) -> Optional[str]:
    try:
        parts = uri_parts(canonicalize_uri(resource_uri, ctx))
    except (NamespaceShapeError, ValueError):
        return None
    if len(parts) >= 5 and parts[0] == "user" and parts[2] == "peers":
        return normalize_peer_id(parts[3])
    return None


def _memory_roots_for_resource_refs(ctx: RequestContext, resource_uri: str) -> List[str]:
    user_root = canonical_user_root(ctx)
    target_peer_id = _resource_reason_peer_id(ctx, resource_uri)
    if target_peer_id:
        return [f"{user_root}/peers/{target_peer_id}/memories"]
    return [f"{user_root}/memories"]


@dataclass
class _MemoryRefMatch:
    memory_uri: str
    memory_file: MemoryFile
    resource_ref: Dict[str, Any]


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
        self._reason_session_lock = asyncio.Lock()

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
        session_id = _RESOURCE_REASON_SESSION_ID
        target_peer_id = _resource_reason_peer_id(ctx, resource_uri)
        commit_result: Dict[str, Any] = {}
        task_result: Optional[Dict[str, Any]] = None

        async with self._reason_session_lock:
            session = await self._session_service.get(
                session_id,
                ctx,
                auto_create=True,
            )
            session.meta.memory_policy = _resource_reason_memory_policy(target_peer_id)
            message_spec: Dict[str, Any] = {
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
            if target_peer_id:
                message_spec["peer_id"] = target_peer_id
            session.add_messages([message_spec])
            commit_result = await self._session_service.commit_async(
                session_id,
                ctx,
                keep_recent_count=0,
            )

        task_id = commit_result.get("task_id")
        if task_id:
            task_result = await self._wait_for_commit_task(
                task_id=str(task_id),
                ctx=ctx,
                timeout=timeout,
            )
        return {
            "status": "success",
            "session_id": session_id,
            "commit_task_id": task_id,
            "archive_uri": commit_result.get("archive_uri"),
            "commit_task": task_result,
        }

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
                    recursive=recursive,
                )
                cleaned.extend(cleanup_result.written_uris + cleanup_result.edited_uris)
                deleted.extend(cleanup_result.deleted_uris)
                if memory_uri in cleanup_result.deleted_uris:
                    continue
                if not cleanup_result.has_changes():
                    await self._remove_resource_refs(
                        memory_uri,
                        resource_uri,
                        ctx,
                        recursive=recursive,
                    )
                    cleaned.append(memory_uri)
                await self._assert_resource_unlinked(
                    memory_uri,
                    resource_uri,
                    ctx,
                    recursive=recursive,
                )
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

    async def _cleanup_memory_reference(
        self,
        *,
        ctx: RequestContext,
        memory_uri: str,
        memory_file: MemoryFile,
        resource_uri: str,
        reason: str,
        recursive: bool = False,
    ) -> MemoryUpdateResult:
        del reason
        viking_fs = self._get_viking_fs()
        current = memory_file
        try:
            raw = await viking_fs.read_file(memory_uri, ctx=ctx)
            current = MemoryFileUtils.read(raw, uri=memory_uri)
        except (NotFoundError, FileNotFoundError):
            result = MemoryUpdateResult()
            result.add_deleted(memory_uri)
            return result

        changed = remove_resource_references_from_memory(
            current,
            resource_uri,
            recursive=recursive,
        )
        result = MemoryUpdateResult()
        if not changed:
            return result

        await viking_fs.write_file(memory_uri, MemoryFileUtils.write(current), ctx=ctx)
        result.add_edited(memory_uri)
        if await self._delete_empty_cleanup_memory(memory_uri, ctx):
            self._mark_result_deleted(result, memory_uri)
        return result

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
        *,
        recursive: bool,
    ) -> None:
        viking_fs = self._get_viking_fs()
        raw = await viking_fs.read_file(memory_uri, ctx=ctx)
        mf = MemoryFileUtils.read(raw, uri=memory_uri)
        refs = [
            ref
            for ref in self._coerce_resource_refs(mf.extra_fields.get("resource_refs"))
            if not self._resource_ref_matches(
                ref.get("resource_uri"),
                resource_uri,
                recursive=recursive,
            )
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
        matches: List[_MemoryRefMatch] = []
        entries: List[Dict[str, Any]] = []
        for memory_root in _memory_roots_for_resource_refs(ctx, resource_uri):
            try:
                entries.extend(
                    await viking_fs.tree(
                        memory_root,
                        ctx=ctx,
                        node_limit=1000000,
                        level_limit=None,
                    )
                )
            except Exception:
                continue

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
            if not any(
                match.memory_uri == uri for match in matches
            ) and content_references_resource(
                mf.content,
                resource_uri,
                recursive=recursive,
            ):
                matched_uri = next(
                    (
                        item
                        for item in extract_resource_uris(mf.content)
                        if self._resource_ref_matches(item, resource_uri, recursive)
                    ),
                    resource_uri,
                )
                matches.append(
                    _MemoryRefMatch(
                        uri,
                        mf,
                        {
                            "resource_uri": matched_uri,
                            "source": "visible_content",
                        },
                    )
                )
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
        *,
        recursive: bool = True,
    ) -> None:
        try:
            raw = await self._get_viking_fs().read_file(memory_uri, ctx=ctx)
        except (NotFoundError, FileNotFoundError) as exc:
            raise NotFoundError(memory_uri, "memory") from exc
        mf = MemoryFileUtils.read(raw, uri=memory_uri)
        if content_references_resource(mf.content, resource_uri, recursive=recursive):
            raise RuntimeError(f"memory content still contains deleted resource URI: {memory_uri}")
        for ref in self._coerce_resource_refs(mf.extra_fields.get("resource_refs")):
            if self._resource_ref_matches(
                ref.get("resource_uri"),
                resource_uri,
                recursive=recursive,
            ):
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
        return resource_ref_matches(ref_uri, target_uri, recursive=recursive)
