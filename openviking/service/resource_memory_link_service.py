# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Link resource additions and resource Wiki overviews to user memories."""

from __future__ import annotations

import asyncio
import re
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
from openviking.message.part import ContextPart, TextPart
from openviking.server.identity import RequestContext
from openviking.service.wiki_link_render_service import WikiLinkRenderService
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.memory_updater import MemoryUpdateResult
from openviking.session.memory.merge_op.link_merge import wiki_links_enabled
from openviking.session.memory.session_extract_context_provider import (
    RESOURCE_WIKI_EXTRACTION_HEADER,
)
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.session.memory.utils.resource_refs import (
    content_references_resource,
    extract_resource_uris,
    resource_ref_matches,
    unlink_resource_references_from_memory,
)
from openviking.storage import VikingDBManager
from openviking.storage.viking_fs import VikingFS, get_viking_fs
from openviking_cli.exceptions import NotFoundError
from openviking_cli.utils import get_logger

if TYPE_CHECKING:
    from openviking.service.session_service import SessionService

logger = get_logger(__name__)

_RESOURCE_REASON_SESSION_ID = "__openviking_resource_reason__"
_RESOURCE_WIKI_SESSION_ID = "__openviking_wiki__"
_RESOURCE_REASON_MEMORY_TYPES = ["entities", "events", "preferences"]
_RESOURCE_DELETION_MEMORY_TYPES = ["entities", "preferences"]
_RESOURCE_WIKI_MEMORY_TYPES = ["entities"]
_RESOURCE_REASON_COMMIT_TIMEOUT_SECONDS = 1800.0
_RESOURCE_ABSTRACT_MAX_CHARS = 200
_ABSTRACT_NOT_READY_MARKERS = (
    "[.abstract.md is not ready]",
    "[Directory abstract is not ready]",
)


def _resource_memory_policy(
    *,
    memory_types: Sequence[str],
    target_peer_id: Optional[str] = None,
) -> Dict[str, Any]:
    peer_targeted = bool(target_peer_id)
    return {
        "self": {"enabled": not peer_targeted},
        "peer": {"enabled": peer_targeted},
        "memory_types": list(memory_types),
    }


def _resource_reason_memory_policy(target_peer_id: Optional[str] = None) -> Dict[str, Any]:
    return _resource_memory_policy(
        memory_types=_RESOURCE_REASON_MEMORY_TYPES,
        target_peer_id=target_peer_id,
    )


def _resource_deletion_memory_policy(target_peer_id: Optional[str] = None) -> Dict[str, Any]:
    # Events are append-only, so deletion commits update only mutable memory types.
    return _resource_memory_policy(
        memory_types=_RESOURCE_DELETION_MEMORY_TYPES,
        target_peer_id=target_peer_id,
    )


def _resource_wiki_memory_policy(target_peer_id: Optional[str] = None) -> Dict[str, Any]:
    policy = _resource_memory_policy(
        memory_types=_RESOURCE_WIKI_MEMORY_TYPES,
        target_peer_id=target_peer_id,
    )
    policy["working_memory"] = {"enabled": False}
    return policy


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
    """Create and clean memory/Wiki references for imported resources."""

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
        self._wiki_render_lock = asyncio.Lock()

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

    async def on_resource_wiki_added(
        self,
        *,
        ctx: RequestContext,
        resource_uri: str,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Extract request-scoped entity Wiki pages from a resource overview."""
        if not wiki_links_enabled():
            return {"status": "skipped", "reason": "link_disabled"}
        if not resource_uri:
            return {"status": "skipped", "reason": "empty_resource_uri"}
        if not self._session_service:
            return {"status": "skipped", "reason": "session_service_unavailable"}

        try:
            canonical_resource_uri = canonicalize_uri(resource_uri, ctx).rstrip("/")
        except (NamespaceShapeError, ValueError):
            return {"status": "skipped", "reason": "invalid_resource_uri"}
        parts = uri_parts(canonical_resource_uri)
        if (
            not parts
            or parts[0] not in {"resources", "user"}
            or context_type_for_uri(canonical_resource_uri) != "resource"
        ):
            return {"status": "skipped", "reason": "unsupported_resource_scope"}

        overview_uri = f"{canonical_resource_uri}/.overview.md"
        try:
            overview_raw = await self._get_viking_fs().read_file(overview_uri, ctx=ctx)
        except Exception:
            return {"status": "skipped", "reason": "overview_unavailable"}

        overview = MemoryFileUtils.read(overview_raw, uri=overview_uri)
        stale_links_removed = len(overview.links) + len(overview.backlinks)

        removed_backlinks = await self._remove_stale_resource_wiki_backlinks(
            ctx=ctx,
            overview_uri=overview_uri,
        )
        created_at = datetime.now(timezone.utc).isoformat()
        resource_abstract = overview.content[:_RESOURCE_ABSTRACT_MAX_CHARS]
        target_peer_id = _resource_reason_peer_id(ctx, canonical_resource_uri)
        result = await self._commit_memory_message(
            ctx=ctx,
            parts=[
                TextPart(text=self._build_resource_wiki_message()),
                ContextPart(
                    uri=overview_uri,
                    context_type="resource",
                    abstract=resource_abstract,
                ),
            ],
            created_at=created_at,
            memory_policy=_resource_wiki_memory_policy(target_peer_id),
            target_peer_id=target_peer_id,
            timeout=timeout,
        )
        wiki_pages_root = self._wiki_pages_root_for_resource(ctx, canonical_resource_uri)
        async with self._wiki_render_lock:
            rendering = await WikiLinkRenderService(self._get_viking_fs()).render(
                ctx=ctx,
                resource_uri=canonical_resource_uri,
                wiki_pages_root=wiki_pages_root,
            )
        result["overview_uri"] = overview_uri
        result["stale_links_removed"] = stale_links_removed
        result["stale_backlinks_removed"] = removed_backlinks
        result["sidecar_rendering"] = rendering
        return result

    @staticmethod
    def _wiki_pages_root_for_resource(ctx: RequestContext, resource_uri: str) -> str:
        root = canonical_user_root(ctx)
        target_peer_id = _resource_reason_peer_id(ctx, resource_uri)
        if target_peer_id:
            root = f"{root}/peers/{target_peer_id}"
        return f"{root}/memories/entities"

    async def on_resource_deleted(
        self,
        *,
        ctx: RequestContext,
        resource_uri: str,
        memory_uris: Sequence[str],
        recursive: bool = False,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Bridge resource deletion through normal session commit for mutable memories."""
        if not resource_uri:
            return {"status": "skipped", "reason": "empty_resource_uri"}
        if not self._session_service:
            return {"status": "skipped", "reason": "session_service_unavailable"}

        deleted_at = datetime.now(timezone.utc).isoformat()
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
            session.meta.memory_policy = _resource_deletion_memory_policy(target_peer_id)
            message_spec: Dict[str, Any] = {
                "role": "user",
                "parts": [
                    TextPart(
                        text=self._build_resource_deletion_message(
                            resource_uri=resource_uri,
                            deleted_at=deleted_at,
                            memory_uris=memory_uris,
                            recursive=recursive,
                        )
                    )
                ],
                "created_at": deleted_at,
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

    async def _commit_memory_message(
        self,
        *,
        ctx: RequestContext,
        parts: Sequence[Any],
        created_at: str,
        memory_policy: Dict[str, Any],
        target_peer_id: Optional[str],
        timeout: Optional[float],
    ) -> Dict[str, Any]:
        session_id = _RESOURCE_WIKI_SESSION_ID
        async with self._reason_session_lock:
            session = await self._session_service.get(session_id, ctx, auto_create=True)
            session.meta.memory_policy = memory_policy
            message_spec: Dict[str, Any] = {
                "role": "user",
                "parts": list(parts),
                "created_at": created_at,
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
        task_result = None
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

    @staticmethod
    def _build_resource_wiki_message() -> str:
        return (
            f"{RESOURCE_WIKI_EXTRACTION_HEADER}\n"
            "Extract durable memories from the provided context. Create links only between "
            "memory pages when meaningful."
        )

    @staticmethod
    def _build_resource_deletion_message(
        *,
        resource_uri: str,
        deleted_at: str,
        memory_uris: Sequence[str],
        recursive: bool,
    ) -> str:
        affected = "\n".join(f"- {uri}" for uri in dict.fromkeys(memory_uris))
        if not affected:
            affected = "- N/A"
        recursive_text = "true" if recursive else "false"
        return (
            "## Resource Deletion\n"
            f"Resource URI: {resource_uri}\n"
            f"Deleted at: {deleted_at or 'N/A'}\n"
            f"Recursive delete: {recursive_text}\n"
            "Affected memory URIs:\n"
            f"{affected}\n\n"
            "Update existing mutable memories that mention or depend on this resource."
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
        """Commit mutable memory updates and unlink stale references to a deleted resource."""
        if context_type_for_uri(resource_uri) != "resource":
            return {"status": "skipped", "reason": "not_resource"}

        include_wiki_links = wiki_links_enabled()
        matches = await self._find_referencing_memories(
            ctx=ctx,
            resource_uri=resource_uri,
            recursive=recursive,
            include_wiki_links=include_wiki_links,
        )
        if not matches:
            return {"status": "no_references", "memory_uris": []}

        memory_uris = self._memory_uris_from_matches(matches)
        try:
            commit_result = await self.on_resource_deleted(
                ctx=ctx,
                resource_uri=resource_uri,
                memory_uris=memory_uris,
                recursive=recursive,
            )
        except Exception as exc:
            logger.warning("Resource deletion memory commit failed for %s: %s", resource_uri, exc)
            commit_result = {"status": "failed", "error": str(exc)}

        post_commit_matches = await self._find_referencing_memories(
            ctx=ctx,
            resource_uri=resource_uri,
            recursive=recursive,
            include_wiki_links=include_wiki_links,
        )

        cleaned: List[str] = []
        deleted: List[str] = []
        errors: List[str] = []
        grouped = self._group_matches_by_memory(post_commit_matches)
        for memory_uri, memory_matches in grouped.items():
            first = memory_matches[0]
            try:
                cleanup_result = await self._unlink_memory_reference(
                    ctx=ctx,
                    memory_uri=memory_uri,
                    memory_file=first.memory_file,
                    resource_uri=resource_uri,
                    recursive=recursive,
                    unlink_wiki_links=include_wiki_links,
                )
                cleaned.extend(cleanup_result.written_uris + cleanup_result.edited_uris)
                deleted.extend(cleanup_result.deleted_uris)
                if memory_uri in cleanup_result.deleted_uris:
                    continue
                await self._assert_resource_unlinked(
                    memory_uri,
                    resource_uri,
                    ctx,
                    recursive=recursive,
                    check_wiki_links=include_wiki_links,
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
            "memory_commit": commit_result,
        }

    async def _unlink_memory_reference(
        self,
        *,
        ctx: RequestContext,
        memory_uri: str,
        memory_file: MemoryFile,
        resource_uri: str,
        recursive: bool = False,
        unlink_wiki_links: bool = False,
    ) -> MemoryUpdateResult:
        viking_fs = self._get_viking_fs()
        current = memory_file
        try:
            raw = await viking_fs.read_file(memory_uri, ctx=ctx)
            current = MemoryFileUtils.read(raw, uri=memory_uri)
        except (NotFoundError, FileNotFoundError):
            result = MemoryUpdateResult()
            result.add_deleted(memory_uri)
            return result

        changed = unlink_resource_references_from_memory(
            current,
            resource_uri,
            recursive=recursive,
            unlink_wiki_links=unlink_wiki_links,
        )
        result = MemoryUpdateResult()
        if not changed:
            return result

        await viking_fs.write_file(memory_uri, MemoryFileUtils.write(current), ctx=ctx)
        result.add_edited(memory_uri)
        return result

    async def _find_referencing_memories(
        self,
        *,
        ctx: RequestContext,
        resource_uri: str,
        recursive: bool,
        include_wiki_links: bool = False,
    ) -> List[_MemoryRefMatch]:
        candidate_uris = await self._grep_candidate_memory_uris(
            ctx=ctx,
            resource_uri=resource_uri,
        )
        if candidate_uris is None:
            logger.warning(
                "Skipping resource memory reference scan for %s because grep is unavailable",
                resource_uri,
            )
            return []
        return await self._read_referencing_memory_matches(
            candidate_uris,
            ctx=ctx,
            resource_uri=resource_uri,
            recursive=recursive,
            include_wiki_links=include_wiki_links,
        )

    async def _remove_stale_resource_wiki_backlinks(
        self,
        *,
        ctx: RequestContext,
        overview_uri: str,
    ) -> int:
        matches = await self._find_referencing_memories(
            ctx=ctx,
            resource_uri=overview_uri,
            recursive=False,
            include_wiki_links=True,
        )
        removed = 0
        for memory_uri, memory_matches in self._group_matches_by_memory(matches).items():
            result = await self._unlink_memory_reference(
                ctx=ctx,
                memory_uri=memory_uri,
                memory_file=memory_matches[0].memory_file,
                resource_uri=overview_uri,
                recursive=False,
                unlink_wiki_links=True,
            )
            if result.edited_uris or result.deleted_uris:
                removed += 1
        return removed

    async def _grep_candidate_memory_uris(
        self,
        *,
        ctx: RequestContext,
        resource_uri: str,
    ) -> Optional[List[str]]:
        """Use backend grep to avoid reading every memory file when supported."""
        viking_fs = self._get_viking_fs()
        if not hasattr(viking_fs, "grep"):
            return None

        search_needle = self._resource_uri_search_needle(resource_uri)
        if not search_needle:
            return []

        candidate_uris: List[str] = []
        for memory_root in _memory_roots_for_resource_refs(ctx, resource_uri):
            try:
                result = await viking_fs.grep(
                    memory_root,
                    pattern=re.escape(search_needle),
                    ctx=ctx,
                    node_limit=None,
                    level_limit=None,
                )
            except (NotFoundError, FileNotFoundError):
                continue
            except Exception as exc:
                logger.debug(
                    "Resource memory grep failed for %s, skipping reference scan: %s",
                    memory_root,
                    exc,
                )
                return None

            for match in result.get("matches", []):
                uri = match.get("uri", "")
                if self._is_memory_markdown_file(uri):
                    candidate_uris.append(uri)

        return list(dict.fromkeys(candidate_uris))

    async def _read_referencing_memory_matches(
        self,
        candidate_uris: Sequence[str],
        *,
        ctx: RequestContext,
        resource_uri: str,
        recursive: bool,
        include_wiki_links: bool = False,
    ) -> List[_MemoryRefMatch]:
        viking_fs = self._get_viking_fs()
        matches: List[_MemoryRefMatch] = []
        search_needle = self._resource_uri_search_needle(resource_uri)
        for uri in candidate_uris:
            try:
                raw = await viking_fs.read_file(uri, ctx=ctx)
                raw_text = raw if isinstance(raw, str) else str(raw or "")
            except Exception:
                continue
            if search_needle and search_needle not in raw_text:
                continue

            try:
                mf = MemoryFileUtils.read(raw_text, uri=uri)
            except Exception:
                continue

            matched = False
            for ref in self._coerce_resource_refs(mf.extra_fields.get("resource_refs")):
                if self._resource_ref_matches(ref.get("resource_uri"), resource_uri, recursive):
                    matches.append(_MemoryRefMatch(uri, mf, ref))
                    matched = True
            if matched:
                continue

            if include_wiki_links:
                for link in list(mf.links or []) + list(mf.backlinks or []):
                    if not isinstance(link, dict):
                        continue
                    if self._resource_ref_matches(
                        link.get("from_uri"), resource_uri, recursive
                    ) or self._resource_ref_matches(link.get("to_uri"), resource_uri, recursive):
                        matches.append(
                            _MemoryRefMatch(
                                uri,
                                mf,
                                {
                                    "resource_uri": resource_uri,
                                    "source": "wiki_link",
                                },
                            )
                        )
                        matched = True
                        break
                if matched:
                    continue

            if content_references_resource(
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

    @staticmethod
    def _resource_uri_search_needle(resource_uri: str) -> str:
        return (resource_uri or "").strip().rstrip("/")

    @staticmethod
    def _is_memory_markdown_file(uri: str) -> bool:
        return (
            isinstance(uri, str)
            and uri.endswith(".md")
            and not uri.endswith("/.abstract.md")
            and not uri.endswith("/.overview.md")
        )

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
        check_wiki_links: bool = False,
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
        if check_wiki_links:
            for link in list(mf.links or []) + list(mf.backlinks or []):
                if not isinstance(link, dict):
                    continue
                if self._resource_ref_matches(
                    link.get("from_uri"), resource_uri, recursive
                ) or self._resource_ref_matches(link.get("to_uri"), resource_uri, recursive):
                    raise RuntimeError(f"memory still contains resource Wiki link: {memory_uri}")

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
    def _memory_uris_from_matches(matches: Sequence[_MemoryRefMatch]) -> List[str]:
        return list(dict.fromkeys(match.memory_uri for match in matches))

    @staticmethod
    def _resource_ref_matches(
        ref_uri: Any,
        target_uri: str,
        recursive: bool,
    ) -> bool:
        return resource_ref_matches(ref_uri, target_uri, recursive=recursive)
