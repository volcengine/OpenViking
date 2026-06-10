# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Coordinator for content write operations."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from openviking.core.namespace import NamespaceShapeError, canonicalize_uri, context_type_for_uri
from openviking.resource.watch_storage import is_watch_task_control_uri
from openviking.server.identity import RequestContext
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.storage.queuefs import SemanticMsg, get_queue_manager
from openviking.storage.queuefs.semantic_msg import build_semantic_coalesce_key
from openviking.storage.transaction import get_lock_manager
from openviking.storage.viking_fs import VikingFS
from openviking.telemetry import get_current_telemetry
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking.telemetry.resource_summary import build_queue_status_payload
from openviking_cli.exceptions import (
    AlreadyExistsError,
    DeadlineExceededError,
    InvalidArgumentError,
    NotFoundError,
)
from openviking_cli.utils import VikingURI
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

_DERIVED_FILENAMES = frozenset({".abstract.md", ".overview.md", ".relations.json"})
_CREATE_ALLOWED_EXTENSIONS = frozenset(
    {".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".py", ".js", ".ts"}
)
_CONTENT_WRITE_RESOURCE_REF_SOURCE = "content.write"
_MARKDOWN_RESOURCE_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((viking://resources/[^)\s]+)\)")
_RESOURCE_URI_RE = re.compile(r"viking://resources/[^\s<>\]\)\"']+")
_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_TRAILING_URI_PUNCTUATION = ".,;:!?，。；：！？"
_SENTENCE_BOUNDARIES = "。！？.!?\n"
_MAX_LINKIFIED_SENTENCE_CHARS = 160


class ContentWriteCoordinator:
    """Write a file (create or modify) and trigger downstream maintenance."""

    def __init__(self, viking_fs: VikingFS):
        self._viking_fs = viking_fs

    async def write(
        self,
        *,
        uri: str,
        content: str,
        ctx: RequestContext,
        mode: str = "replace",
        wait: bool = False,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        try:
            normalized_uri = canonicalize_uri(uri, ctx)
        except NamespaceShapeError as exc:
            raise InvalidArgumentError(str(exc)) from exc
        self._validate_mode(mode)
        self._validate_target_uri(normalized_uri)

        if mode == "create":
            return await self._create_and_write(
                uri=normalized_uri,
                content=content,
                ctx=ctx,
                wait=wait,
                timeout=timeout,
            )

        stat = await self._safe_stat(normalized_uri, ctx=ctx)
        if stat.get("isDir"):
            raise InvalidArgumentError(f"write only supports existing files, got directory: {uri}")

        context_type = context_type_for_uri(normalized_uri)
        root_uri = await self._resolve_root_uri(normalized_uri, ctx=ctx)
        written_bytes = len(content.encode("utf-8"))
        telemetry_id = get_current_telemetry().telemetry_id

        if context_type == "memory":
            return await self._write_memory_with_refresh(
                uri=normalized_uri,
                root_uri=root_uri,
                content=content,
                mode=mode,
                wait=wait,
                timeout=timeout,
                ctx=ctx,
                written_bytes=written_bytes,
                telemetry_id=telemetry_id,
            )

        return await self._write_direct_with_refresh(
            uri=normalized_uri,
            root_uri=root_uri,
            content=content,
            mode=mode,
            context_type=context_type,
            wait=wait,
            timeout=timeout,
            ctx=ctx,
            written_bytes=written_bytes,
            telemetry_id=telemetry_id,
        )

    def _build_write_result(
        self,
        *,
        uri: str,
        root_uri: str,
        context_type: str,
        mode: str,
        written_bytes: int,
        wait: bool,
        queue_status: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        semantic_status, vector_status = self._refresh_statuses(
            wait=wait,
            queue_status=queue_status,
        )
        return {
            "uri": uri,
            "root_uri": root_uri,
            "context_type": context_type,
            "mode": mode,
            "written_bytes": written_bytes,
            "content_updated": True,
            "semantic_status": semantic_status,
            "vector_status": vector_status,
            "queue_status": queue_status,
        }

    def _refresh_statuses(
        self,
        *,
        wait: bool,
        queue_status: Optional[Dict[str, Any]],
    ) -> tuple[str, str]:
        if not wait:
            return "queued", "queued"
        if not queue_status:
            return "complete", "complete"

        def _has_errors(name: str) -> bool:
            status = queue_status.get(name, {})
            if not isinstance(status, dict):
                return False
            try:
                return int(status.get("error_count", 0) or 0) > 0
            except (TypeError, ValueError):
                return bool(status.get("errors"))

        semantic_status = "failed" if _has_errors("Semantic") else "complete"
        vector_status = "failed" if _has_errors("Embedding") else "complete"
        return semantic_status, vector_status

    async def _write_direct_with_refresh(
        self,
        *,
        uri: str,
        root_uri: str,
        content: str,
        mode: str,
        context_type: str,
        wait: bool,
        timeout: Optional[float],
        ctx: RequestContext,
        written_bytes: int,
        telemetry_id: str,
    ) -> Dict[str, Any]:
        lock_manager = get_lock_manager()
        handle = lock_manager.create_handle()
        lock_path = self._viking_fs._uri_to_path(uri, ctx=ctx)
        acquired = await lock_manager.acquire_exact_path(handle, lock_path)
        if not acquired:
            await lock_manager.release(handle)
            raise InvalidArgumentError(f"resource is busy and cannot be written now: {uri}")

        previous_content: Optional[str] = None
        content_written = False
        semantic_enqueued = False
        lock_released = False
        try:
            if mode != "create":
                previous_content = await self._viking_fs.read_file(uri, ctx=ctx)
            if wait and telemetry_id:
                get_request_wait_tracker().register_request(telemetry_id)
            await self._write_in_place(uri, content, mode=mode, ctx=ctx)
            content_written = True
            await self._enqueue_semantic_refresh(
                root_uri=root_uri,
                changed_uri=uri,
                context_type=context_type,
                ctx=ctx,
                change_type="added" if mode == "create" else "modified",
            )
            semantic_enqueued = True
            await lock_manager.release(handle)
            lock_released = True
            queue_status = (
                await self._wait_for_request(telemetry_id=telemetry_id, timeout=timeout)
                if wait
                else None
            )
            return self._build_write_result(
                uri=uri,
                root_uri=root_uri,
                context_type=context_type,
                mode=mode,
                written_bytes=written_bytes,
                wait=wait,
                queue_status=queue_status,
            )
        except Exception:
            if not semantic_enqueued and content_written:
                await self._rollback_direct_write(
                    uri=uri,
                    previous_content=previous_content,
                    mode=mode,
                    ctx=ctx,
                    lock_handle=handle,
                )
            if not lock_released:
                await lock_manager.release(handle)
            raise
        finally:
            if wait and telemetry_id:
                get_request_wait_tracker().cleanup(telemetry_id)

    async def _rollback_direct_write(
        self,
        *,
        uri: str,
        previous_content: Optional[str],
        mode: str,
        ctx: RequestContext,
        lock_handle: Any,
    ) -> None:
        try:
            if mode == "create":
                await self._viking_fs.rm(uri, ctx=ctx, lock_handle=lock_handle)
                return
            if previous_content is not None:
                await self._viking_fs.write_file(uri, previous_content, ctx=ctx)
        except Exception:
            logger.error("Failed to rollback direct content write for %s", uri, exc_info=True)

    def _validate_mode(self, mode: str) -> None:
        if mode not in {"replace", "append", "create"}:
            raise InvalidArgumentError(f"unsupported write mode: {mode}")

    def _validate_target_uri(self, uri: str) -> None:
        name = uri.rstrip("/").split("/")[-1]
        if name in _DERIVED_FILENAMES:
            raise InvalidArgumentError(f"cannot write derived semantic file directly: {uri}")
        if is_watch_task_control_uri(uri):
            raise InvalidArgumentError(f"cannot write watch task control file directly: {uri}")

        parsed = VikingURI(uri)
        if parsed.scope not in {"resources", "user", "agent"}:
            raise InvalidArgumentError(f"write is not supported for scope: {parsed.scope}")

    def _is_not_found(self, exc: Exception) -> bool:
        """Check if an exception indicates a not-found error (OpenViking or AGFS)."""
        if isinstance(exc, NotFoundError):
            return True
        # AGFS raises its own AGFSNotFoundError which is unrelated to our NotFoundError
        try:
            from openviking.pyagfs import AGFSNotFoundError

            return isinstance(exc, AGFSNotFoundError)
        except ImportError:
            return False

    async def _safe_stat(
        self, uri: str, *, ctx: RequestContext, allow_not_found: bool = False
    ) -> Dict[str, Any]:
        try:
            return await self._viking_fs.stat(uri, ctx=ctx)
        except Exception as exc:
            if self._is_not_found(exc):
                if allow_not_found:
                    return {"not_found": True}
                if isinstance(exc, NotFoundError):
                    raise
                raise NotFoundError(uri, "file") from exc
            raise NotFoundError(uri, "file") from exc

    def _validate_create_extension(self, uri: str) -> None:
        _, ext = os.path.splitext(uri)
        if ext.lower() not in _CREATE_ALLOWED_EXTENSIONS:
            raise InvalidArgumentError(f"create mode does not allow extension '{ext}': {uri}")

    async def _create_and_write(
        self,
        *,
        uri: str,
        content: str,
        ctx: RequestContext,
        wait: bool,
        timeout: Optional[float],
    ) -> Dict[str, Any]:
        self._validate_create_extension(uri)

        stat = await self._safe_stat(uri, ctx=ctx, allow_not_found=True)
        if not stat.get("not_found"):
            raise AlreadyExistsError(uri, "file")

        context_type = context_type_for_uri(uri)
        root_uri = await self._resolve_root_uri(uri, ctx=ctx, _allow_not_found=True)
        written_bytes = len(content.encode("utf-8"))
        telemetry_id = get_current_telemetry().telemetry_id

        if context_type == "memory":
            return await self._write_memory_with_refresh(
                uri=uri,
                root_uri=root_uri,
                content=content,
                mode="create",
                wait=wait,
                timeout=timeout,
                ctx=ctx,
                written_bytes=written_bytes,
                telemetry_id=telemetry_id,
            )

        return await self._write_direct_with_refresh(
            uri=uri,
            root_uri=root_uri,
            content=content,
            mode="create",
            context_type=context_type,
            wait=wait,
            timeout=timeout,
            ctx=ctx,
            written_bytes=written_bytes,
            telemetry_id=telemetry_id,
        )

    async def _write_in_place(
        self,
        uri: str,
        content: str,
        *,
        mode: str,
        ctx: RequestContext,
    ) -> None:
        if context_type_for_uri(uri) == "memory":
            if mode == "replace":
                existing_raw = await self._viking_fs.read_file(uri, ctx=ctx)
                mf = MemoryFileUtils.read(existing_raw, uri=uri)
                mf.content = content
            elif mode == "append":
                existing_raw = await self._viking_fs.read_file(uri, ctx=ctx)
                mf = MemoryFileUtils.read(existing_raw, uri=uri)
                mf.content = mf.content + content
            else:
                mf = MemoryFileUtils.read(content, uri=uri)
            self._sync_memory_resource_refs(mf)
            await self._viking_fs.write_file(uri, MemoryFileUtils.write(mf), ctx=ctx)
            return

        if mode == "append":
            existing_raw = await self._viking_fs.read_file(uri, ctx=ctx)
            mf = MemoryFileUtils.read(existing_raw, uri=uri)
            mf.content = mf.content + content
            updated_raw = MemoryFileUtils.write(mf)
            await self._viking_fs.write_file(uri, updated_raw, ctx=ctx)
            return
        await self._viking_fs.write_file(uri, content, ctx=ctx)

    def _sync_memory_resource_refs(self, mf) -> None:
        code_spans = self._protected_code_spans(mf.content)
        markdown_refs, markdown_spans = self._extract_markdown_resource_refs(
            mf.content,
            code_spans,
        )
        mf.content, bare_refs = self._linkify_bare_resource_uris(
            mf.content,
            code_spans + markdown_spans,
        )
        self._merge_content_write_resource_refs(mf, markdown_refs + bare_refs)

    @staticmethod
    def _protected_code_spans(content: str) -> List[tuple[int, int]]:
        spans = [(match.start(), match.end()) for match in _CODE_BLOCK_RE.finditer(content or "")]
        spans.extend((match.start(), match.end()) for match in _INLINE_CODE_RE.finditer(content or ""))
        return spans

    @classmethod
    def _extract_markdown_resource_refs(
        cls,
        content: str,
        protected_spans: Sequence[tuple[int, int]],
    ) -> tuple[List[Dict[str, Any]], List[tuple[int, int]]]:
        refs: List[Dict[str, Any]] = []
        link_spans: List[tuple[int, int]] = []
        for match in _MARKDOWN_RESOURCE_LINK_RE.finditer(content or ""):
            if cls._overlaps_spans(match.start(), match.end(), protected_spans):
                continue
            label = match.group(1).strip()
            resource_uri = cls._trim_resource_uri(match.group(2).strip())
            link_spans.append((match.start(), match.end()))
            refs.append(
                {
                    "resource_uri": resource_uri,
                    "match_text": label or None,
                }
            )
        return refs, link_spans

    @classmethod
    def _linkify_bare_resource_uris(
        cls,
        content: str,
        protected_spans: Sequence[tuple[int, int]],
    ) -> tuple[str, List[Dict[str, Any]]]:
        refs: List[Dict[str, Any]] = []
        updated = content or ""
        covered_start = len(updated) + 1

        matches = list(_RESOURCE_URI_RE.finditer(updated))
        for match in reversed(matches):
            resource_uri = cls._trim_resource_uri(match.group(0))
            if not resource_uri:
                continue
            start = match.start()
            end = start + len(resource_uri)
            if cls._overlaps_spans(start, end, protected_spans):
                continue

            refs.append({"resource_uri": resource_uri})
            sentence_span = cls._previous_sentence_span(updated, start)
            if not sentence_span:
                continue
            sentence_start, sentence_end = sentence_span
            if end > covered_start:
                continue
            anchor = updated[sentence_start:sentence_end]
            if "viking://resources/" in anchor or "](" in anchor:
                continue
            refs[-1]["match_text"] = anchor
            replacement = f"[{anchor}]({resource_uri})"
            updated = updated[:sentence_start] + replacement + updated[end:]
            covered_start = sentence_start

        refs.reverse()
        return updated, refs

    @staticmethod
    def _previous_sentence_span(content: str, uri_start: int) -> Optional[tuple[int, int]]:
        sentence_end = uri_start
        while sentence_end > 0 and content[sentence_end - 1].isspace():
            sentence_end -= 1
        if sentence_end <= 0:
            return None

        boundary_search_end = sentence_end
        if content[sentence_end - 1] in _SENTENCE_BOUNDARIES:
            boundary_search_end = sentence_end - 1
        sentence_start = 0
        for idx in range(boundary_search_end - 1, -1, -1):
            if content[idx] in _SENTENCE_BOUNDARIES:
                sentence_start = idx + 1
                break
        while sentence_start < sentence_end and content[sentence_start].isspace():
            sentence_start += 1

        anchor = content[sentence_start:sentence_end]
        if not anchor or len(anchor) > _MAX_LINKIFIED_SENTENCE_CHARS:
            return None
        return sentence_start, sentence_end

    @staticmethod
    def _trim_resource_uri(resource_uri: str) -> str:
        return (resource_uri or "").rstrip(_TRAILING_URI_PUNCTUATION)

    @staticmethod
    def _overlaps_spans(
        start: int,
        end: int,
        protected_spans: Sequence[tuple[int, int]],
    ) -> bool:
        return any(start < span_end and end > span_start for span_start, span_end in protected_spans)

    @classmethod
    def _merge_content_write_resource_refs(cls, mf, refs: Sequence[Dict[str, Any]]) -> None:
        visible_refs: Dict[str, Dict[str, Any]] = {}
        for ref in refs:
            resource_uri = ref.get("resource_uri")
            if not isinstance(resource_uri, str) or not resource_uri:
                continue
            existing = visible_refs.setdefault(resource_uri, {"resource_uri": resource_uri})
            match_text = ref.get("match_text")
            if match_text and not existing.get("match_text"):
                existing["match_text"] = match_text

        existing_refs = cls._coerce_resource_refs(mf.extra_fields.get("resource_refs"))
        merged: List[Dict[str, Any]] = []
        seen_resource_uris: set[str] = set()
        created_at = datetime.now(timezone.utc).isoformat()
        for existing_ref in existing_refs:
            resource_uri = existing_ref.get("resource_uri")
            if not isinstance(resource_uri, str) or not resource_uri:
                merged.append(existing_ref)
                continue
            visible_ref = visible_refs.get(resource_uri)
            if (
                existing_ref.get("source") == _CONTENT_WRITE_RESOURCE_REF_SOURCE
                and visible_ref is None
            ):
                continue
            if visible_ref and existing_ref.get("source") == _CONTENT_WRITE_RESOURCE_REF_SOURCE:
                if visible_ref.get("match_text"):
                    existing_ref["match_text"] = visible_ref["match_text"]
                existing_ref.setdefault("created_at", created_at)
            merged.append(existing_ref)
            seen_resource_uris.add(resource_uri)

        for resource_uri, visible_ref in visible_refs.items():
            if resource_uri in seen_resource_uris:
                continue
            ref = {
                "resource_uri": resource_uri,
                "source": _CONTENT_WRITE_RESOURCE_REF_SOURCE,
                "created_at": created_at,
            }
            if visible_ref.get("match_text"):
                ref["match_text"] = visible_ref["match_text"]
            merged.append(ref)

        if merged:
            mf.extra_fields["resource_refs"] = merged
        else:
            mf.extra_fields.pop("resource_refs", None)

    @staticmethod
    def _coerce_resource_refs(value: Any) -> List[Dict[str, Any]]:
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            return [dict(value)]
        return []

    async def _enqueue_semantic_refresh(
        self,
        *,
        root_uri: str,
        changed_uri: str,
        context_type: str,
        ctx: RequestContext,
        change_type: str = "modified",
        target_uri: str = "",
    ) -> None:
        queue_manager = get_queue_manager()
        semantic_queue = queue_manager.get_queue(queue_manager.SEMANTIC, allow_create=True)
        telemetry = get_current_telemetry()
        msg = SemanticMsg(
            uri=root_uri,
            target_uri=target_uri,
            context_type=context_type,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
            peer_id=ctx.user.user_id,
            role=ctx.role.value,
            skip_vectorization=False,
            telemetry_id=telemetry.telemetry_id,
            coalesce_key=(
                build_semantic_coalesce_key(
                    context_type=context_type,
                    uri=root_uri,
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                    peer_id=ctx.user.user_id,
                )
                if context_type in {"resource", "skill"}
                else ""
            ),
            changes={change_type: [changed_uri]},
        )
        if msg.telemetry_id:
            get_request_wait_tracker().register_semantic_root(msg.telemetry_id, msg.id)
        try:
            await semantic_queue.enqueue(msg)
        except Exception as e:
            if msg.telemetry_id:
                get_request_wait_tracker().mark_semantic_failed(msg.telemetry_id, msg.id, str(e))
            raise

    async def _enqueue_memory_refresh(
        self,
        *,
        root_uri: str,
        modified_uri: str,
        ctx: RequestContext,
    ) -> None:
        queue_manager = get_queue_manager()
        semantic_queue = queue_manager.get_queue(queue_manager.SEMANTIC, allow_create=True)
        telemetry = get_current_telemetry()
        msg = SemanticMsg(
            uri=root_uri,
            context_type="memory",
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
            peer_id=ctx.user.user_id,
            role=ctx.role.value,
            skip_vectorization=False,
            telemetry_id=telemetry.telemetry_id,
            coalesce_key=build_semantic_coalesce_key(
                context_type="memory",
                uri=root_uri,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
                peer_id=ctx.user.user_id,
            ),
            changes={"modified": [modified_uri]},
        )
        if msg.telemetry_id:
            get_request_wait_tracker().register_semantic_root(msg.telemetry_id, msg.id)
        try:
            await semantic_queue.enqueue(msg)
        except Exception as e:
            if msg.telemetry_id:
                get_request_wait_tracker().mark_semantic_failed(msg.telemetry_id, msg.id, str(e))
            raise

    async def _wait_for_queues(self, *, timeout: Optional[float]) -> Dict[str, Any]:
        queue_manager = get_queue_manager()
        try:
            status = await queue_manager.wait_complete(timeout=timeout)
        except TimeoutError as exc:
            raise DeadlineExceededError("queue processing", timeout) from exc
        return build_queue_status_payload(status)

    async def _wait_for_request(
        self,
        *,
        telemetry_id: str,
        timeout: Optional[float],
    ) -> Dict[str, Any]:
        if not telemetry_id:
            return await self._wait_for_queues(timeout=timeout)
        tracker = get_request_wait_tracker()
        try:
            await tracker.wait_for_request(telemetry_id, timeout=timeout)
        except TimeoutError as exc:
            raise DeadlineExceededError("queue processing", timeout) from exc
        return tracker.build_queue_status(telemetry_id)

    async def _write_memory_with_refresh(
        self,
        *,
        uri: str,
        root_uri: str,
        content: str,
        mode: str,
        wait: bool,
        timeout: Optional[float],
        ctx: RequestContext,
        written_bytes: int,
        telemetry_id: str,
    ) -> Dict[str, Any]:
        lock_manager = get_lock_manager()
        handle = lock_manager.create_handle()
        lock_path = self._viking_fs._uri_to_path(uri, ctx=ctx)
        acquired = await lock_manager.acquire_exact_path(handle, lock_path)
        if not acquired:
            await lock_manager.release(handle)
            raise InvalidArgumentError(f"resource is busy and cannot be written now: {uri}")

        released = False
        try:
            if wait and telemetry_id:
                get_request_wait_tracker().register_request(telemetry_id)
            await self._write_in_place(uri, content, mode=mode, ctx=ctx)
            await self._enqueue_memory_refresh(
                root_uri=root_uri,
                modified_uri=uri,
                ctx=ctx,
            )
            await lock_manager.release(handle)
            released = True
            queue_status = (
                await self._wait_for_request(telemetry_id=telemetry_id, timeout=timeout)
                if wait
                else None
            )
            return self._build_write_result(
                uri=uri,
                root_uri=root_uri,
                context_type="memory",
                mode=mode,
                written_bytes=written_bytes,
                wait=wait,
                queue_status=queue_status,
            )
        except Exception:
            if not released:
                await lock_manager.release(handle)
            raise
        finally:
            if wait and telemetry_id:
                get_request_wait_tracker().cleanup(telemetry_id)

    async def _resolve_root_uri(
        self,
        uri: str,
        *,
        ctx: RequestContext,
        _allow_not_found: bool = False,
    ) -> str:
        parsed = VikingURI(uri)
        parts = [part for part in parsed.full_path.split("/") if part]
        if not parts:
            raise InvalidArgumentError(f"invalid write uri: {uri}")

        root_uri = uri
        if parts[0] == "resources":
            if len(parts) >= 2:
                root_uri = VikingURI.build("resources", parts[1])
        elif parts[0] == "user":
            try:
                memories_idx = parts.index("memories")
            except ValueError as exc:
                raise InvalidArgumentError(
                    f"write only supports memory files under user scope: {uri}"
                ) from exc
            if len(parts) <= memories_idx + 1:
                raise InvalidArgumentError(
                    f"memory write target must be inside a memory type directory: {uri}"
                )
            root_uri = VikingURI.build(*parts[: memories_idx + 2])

        stat = await self._safe_stat(root_uri, ctx=ctx, allow_not_found=_allow_not_found)
        if stat.get("not_found") or not stat.get("isDir"):
            parent = VikingURI(uri).parent
            if parent is None:
                raise InvalidArgumentError(f"could not resolve write root for {uri}")
            root_uri = parent.uri
        return root_uri
