# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compact resource-linked memories created from add-resource reasons."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, Field

from openviking.core.namespace import canonical_user_root, context_type_for_uri
from openviking.models.vlm.base import VLMResponse
from openviking.prompts.manager import render_prompt
from openviking.server.identity import RequestContext, Role
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.memory_updater import MemoryUpdater
from openviking.session.memory.utils import resolve_output_language
from openviking.session.memory.utils.json_parser import parse_json_with_stability
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.storage import VikingDBManager
from openviking.storage.queuefs.named_queue import DequeueHandlerBase
from openviking.storage.queuefs.queue_manager import QueueManager
from openviking.storage.transaction.lock_context import LockContext
from openviking.storage.transaction.lock_manager import get_lock_manager
from openviking.storage.viking_fs import VikingFS, get_viking_fs
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)

RESOURCE_LINK_MEMORY_TYPE = "resource_link_memories"
RESOURCE_LINK_COMPACTION_QUEUE = "ResourceLinkCompaction"
RESOURCE_LINK_MANAGED_FIELD = "resource_link_managed"
RESOURCE_LINK_SOURCE_FIELD = "resource_link_source"
RESOURCE_LINK_CREATED_AT_FIELD = "resource_link_created_at"
RESOURCE_LINK_STATE_FIELD = "resource_link_state"
RESOURCE_LINK_COMPACTION_REF_SOURCE = "resource_link.compaction"

_COMPACTION_THRESHOLD = 10
_COMPACTION_BATCH_SIZE = 50
_TARGET_AGGREGATE_MEMORY_COUNT = 3
_MAX_REPRESENTATIVE_LINKS = 5
_MAX_REASON_CHARS = 180
_MAX_AGGREGATE_CONTENT_CHARS = 1200
_MAX_VISIBLE_CONTENT_CHARS = 360
_MAX_TITLE_CHARS = 24
_RESOURCE_URI_RE = re.compile(r"viking://resources/[^\s<>\]\)\"']+")
_UNSAFE_FILENAME_CHARS_RE = re.compile(r"[\\/:\*\?\"<>\|\n\r\t]+")
_TITLE_DATE_PREFIX_RE = re.compile(
    r"^(?:\d{4}年\d{1,2}月\d{1,2}日|\d{4}[-/]\d{1,2}[-/]\d{1,2})"
)
_TITLE_USER_ACTION_PREFIX_RE = re.compile(r"^(?:用户|该用户|当前用户)(?:上传|保存|添加|导入)的?")


@dataclass
class _CompactionCandidate:
    uri: str
    raw_hash: str
    content: str
    extra_fields: Dict[str, Any]
    resource_refs: List[Dict[str, Any]]
    resource_uris: List[str]

    @property
    def item_count(self) -> int:
        state = self.extra_fields.get(RESOURCE_LINK_STATE_FIELD)
        if isinstance(state, dict):
            try:
                return max(1, int(state.get("item_count") or 0))
            except (TypeError, ValueError):
                pass
        return 1


class _CompactedMemory(BaseModel):
    title: str = ""
    content: str = ""
    resource_uris: List[str] = Field(default_factory=list)
    item_count: int = 0


class _CompactionResponse(BaseModel):
    memories: List[_CompactedMemory] = Field(default_factory=list)


class ResourceLinkMemoryCompactor(DequeueHandlerBase):
    """Batch compact system-managed resource-link memories."""

    def __init__(
        self,
        *,
        vikingdb: Optional[VikingDBManager] = None,
        viking_fs: Optional[VikingFS] = None,
        queue_manager: Optional[QueueManager] = None,
    ):
        self._vikingdb = vikingdb
        self._viking_fs = viking_fs
        self._queue_manager = queue_manager
        self._coalesce_versions: Dict[str, int] = {}
        self._coalesce_lock = threading.Lock()
        if queue_manager:
            self._ensure_queue()

    def set_dependencies(
        self,
        *,
        vikingdb: Optional[VikingDBManager],
        viking_fs: VikingFS,
        queue_manager: Optional[QueueManager],
    ) -> None:
        self._vikingdb = vikingdb
        self._viking_fs = viking_fs
        self._queue_manager = queue_manager
        if queue_manager:
            self._ensure_queue()

    def _get_viking_fs(self) -> VikingFS:
        return self._viking_fs or get_viking_fs()

    def _ensure_queue(self) -> None:
        if not self._queue_manager:
            return
        self._queue_manager.get_queue(
            RESOURCE_LINK_COMPACTION_QUEUE,
            dequeue_handler=self,
            allow_create=True,
        )

    async def mark_managed_memories(
        self,
        *,
        ctx: RequestContext,
        memory_uris: Sequence[str],
        created_at: str,
    ) -> List[str]:
        """Mark newly-created add-resource memories as eligible for later compaction."""
        marked: List[str] = []
        viking_fs = self._get_viking_fs()
        for memory_uri in dict.fromkeys(memory_uris):
            if context_type_for_uri(memory_uri) != "memory":
                continue
            try:
                raw = await viking_fs.read_file(memory_uri, ctx=ctx)
                mf = MemoryFileUtils.read(raw, uri=memory_uri)
            except Exception as exc:
                logger.warning("Failed to mark resource-linked memory %s: %s", memory_uri, exc)
                continue
            if mf.memory_type == RESOURCE_LINK_MEMORY_TYPE:
                continue
            mf.extra_fields[RESOURCE_LINK_MANAGED_FIELD] = True
            mf.extra_fields[RESOURCE_LINK_SOURCE_FIELD] = "add_resource.reason"
            mf.extra_fields.setdefault(RESOURCE_LINK_CREATED_AT_FIELD, created_at)
            await viking_fs.write_file(memory_uri, MemoryFileUtils.write(mf), ctx=ctx)
            marked.append(memory_uri)
        return marked

    async def enqueue_check(self, *, ctx: RequestContext) -> Optional[str]:
        """Enqueue a coalesced compaction check for the current user memory root."""
        if not self._queue_manager:
            return None
        self._ensure_queue()
        key = self._coalesce_key(ctx)
        with self._coalesce_lock:
            version = self._coalesce_versions.get(key, 0) + 1
            self._coalesce_versions[key] = version
        queue = self._queue_manager.get_queue(
            RESOURCE_LINK_COMPACTION_QUEUE,
            dequeue_handler=self,
            allow_create=True,
        )
        return await queue.enqueue(
            {
                "account_id": ctx.account_id,
                "user_id": ctx.user.user_id,
                "role": str(ctx.role.value if hasattr(ctx.role, "value") else ctx.role),
                "coalesce_key": key,
                "coalesce_version": version,
            }
        )

    async def on_dequeue(self, data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        data = self._unwrap_queue_data(data)
        if not data:
            self.report_success()
            return data

        key = str(data.get("coalesce_key") or "")
        version = self._safe_int(data.get("coalesce_version"), default=0)
        if self._is_stale(key, version):
            self.report_success()
            return data

        try:
            ctx = RequestContext(
                user=UserIdentifier(str(data["account_id"]), str(data["user_id"])),
                role=Role(str(data.get("role") or Role.USER.value)),
            )
            result = await self.compact_if_needed(ctx=ctx)
            data["result"] = result
            self.report_success()
        except Exception as exc:
            logger.warning("Resource-link compaction failed: %s", exc, exc_info=True)
            data["error"] = str(exc)
            self.report_error(str(exc), data)
        return data

    async def compact_if_needed(self, *, ctx: RequestContext) -> Dict[str, Any]:
        aggregate_dir_uri = self.aggregate_dir_uri(ctx)
        try:
            lock_manager = get_lock_manager()
            lock_path = self._lock_path(aggregate_dir_uri, ctx)
        except Exception:
            lock_manager = None
            lock_path = ""

        if lock_manager and lock_path:
            async with LockContext(lock_manager, [lock_path], lock_mode="exact"):
                return await self._compact_once(ctx=ctx, aggregate_dir_uri=aggregate_dir_uri)
        return await self._compact_once(ctx=ctx, aggregate_dir_uri=aggregate_dir_uri)

    @staticmethod
    def aggregate_dir_uri(ctx: RequestContext) -> str:
        return f"{canonical_user_root(ctx)}/memories/{RESOURCE_LINK_MEMORY_TYPE}"

    async def _compact_once(
        self,
        *,
        ctx: RequestContext,
        aggregate_dir_uri: str,
    ) -> Dict[str, Any]:
        singles, aggregates = await self._scan_candidates(ctx=ctx, aggregate_dir_uri=aggregate_dir_uri)
        total_memory_count = len(singles) + len(aggregates)
        should_compact = (
            len(singles) >= _COMPACTION_THRESHOLD
            or (len(singles) > 0 and total_memory_count >= _COMPACTION_THRESHOLD)
            or len(aggregates) > _TARGET_AGGREGATE_MEMORY_COUNT
        )
        if not should_compact:
            return {
                "status": "skipped",
                "reason": "below_threshold",
                "single_count": len(singles),
                "aggregate_count": len(aggregates),
                "total_memory_count": total_memory_count,
            }

        batch = singles[:_COMPACTION_BATCH_SIZE]
        response = await self._generate_compaction(
            ctx=ctx,
            batch=batch,
            aggregates=aggregates,
        )
        if not response.memories:
            return {
                "status": "skipped",
                "reason": "empty_compaction_output",
                "single_count": len(singles),
                "aggregate_count": len(aggregates),
                "total_memory_count": total_memory_count,
            }

        written_uris = await self._write_aggregate_memories(
            ctx=ctx,
            aggregate_dir_uri=aggregate_dir_uri,
            response=response,
            input_item_count=sum(item.item_count for item in batch + aggregates),
        )
        if not written_uris:
            return {"status": "skipped", "reason": "no_aggregate_written"}

        target_uris = set(written_uris)
        deleted_uris = await self._delete_compacted_inputs(
            ctx=ctx,
            candidates=[*batch, *aggregates],
            keep_uris=target_uris,
        )
        await self._refresh_deleted_parent_overviews(ctx=ctx, deleted_uris=deleted_uris)

        remaining_singles = max(0, len(singles) - len(batch))
        if remaining_singles >= _COMPACTION_THRESHOLD:
            await self.enqueue_check(ctx=ctx)

        return {
            "status": "success",
            "written_uris": written_uris,
            "deleted_uris": deleted_uris,
            "remaining_single_count": remaining_singles,
        }

    async def _scan_candidates(
        self,
        *,
        ctx: RequestContext,
        aggregate_dir_uri: str,
    ) -> tuple[List[_CompactionCandidate], List[_CompactionCandidate]]:
        viking_fs = self._get_viking_fs()
        memory_root = f"{canonical_user_root(ctx)}/memories"
        try:
            entries = await viking_fs.tree(
                memory_root,
                ctx=ctx,
                node_limit=1000000,
                level_limit=None,
            )
        except Exception as exc:
            logger.warning("Failed to scan memories for resource-link compaction: %s", exc)
            return [], []

        singles: List[_CompactionCandidate] = []
        aggregates: List[_CompactionCandidate] = []
        for entry in entries:
            uri = str(entry.get("uri") or "")
            if not uri or bool(entry.get("isDir") or entry.get("is_dir")):
                continue
            if not uri.endswith(".md") or self._is_hidden_memory_file(uri):
                continue
            try:
                raw = await viking_fs.read_file(uri, ctx=ctx)
                mf = MemoryFileUtils.read(raw, uri=uri)
            except Exception:
                continue

            refs = self._coerce_resource_refs(mf.extra_fields.get("resource_refs"))
            resource_uris = self._resource_uris_from_memory(mf, refs)
            candidate = _CompactionCandidate(
                uri=uri,
                raw_hash=self._hash_raw(raw),
                content=mf.content or "",
                extra_fields=dict(mf.extra_fields or {}),
                resource_refs=refs,
                resource_uris=resource_uris,
            )
            if uri.startswith(aggregate_dir_uri.rstrip("/") + "/"):
                aggregates.append(candidate)
            elif mf.extra_fields.get(RESOURCE_LINK_MANAGED_FIELD) is True and refs:
                singles.append(candidate)

        singles.sort(key=self._candidate_sort_key)
        aggregates.sort(key=lambda item: item.uri)
        return singles, aggregates

    async def _generate_compaction(
        self,
        *,
        ctx: RequestContext,
        batch: Sequence[_CompactionCandidate],
        aggregates: Sequence[_CompactionCandidate],
    ) -> _CompactionResponse:
        prompt = render_prompt(
            "processing.resource_link_memory_compaction",
            {
                "output_language": self._output_language(batch, aggregates),
                "target_memory_count": str(_TARGET_AGGREGATE_MEMORY_COUNT),
                "max_resource_links": str(_MAX_REPRESENTATIVE_LINKS),
                "user_id": ctx.user.user_id,
                "aggregate_memories_json": json.dumps(
                    [self._aggregate_prompt_item(item) for item in aggregates],
                    ensure_ascii=False,
                    indent=2,
                ),
                "resource_items_json": json.dumps(
                    [self._single_prompt_item(item) for item in batch],
                    ensure_ascii=False,
                    indent=2,
                ),
            },
        )
        content = await self._call_model(prompt)
        parsed, error = parse_json_with_stability(content, _CompactionResponse)
        if error or not parsed:
            logger.warning("Failed to parse resource-link compaction output: %s", error)
            return _CompactionResponse()
        return parsed

    async def _call_model(self, prompt: str) -> str:
        config = get_openviking_config()
        vlm = config.vlm.get_vlm_instance()
        response = await vlm.get_completion_async(prompt)
        if isinstance(response, VLMResponse):
            return response.content or ""
        return str(response or "")

    async def _write_aggregate_memories(
        self,
        *,
        ctx: RequestContext,
        aggregate_dir_uri: str,
        response: _CompactionResponse,
        input_item_count: int,
    ) -> List[str]:
        now = datetime.now(timezone.utc).isoformat()
        used_names: set[str] = set()
        written_uris: List[str] = []
        viking_fs = self._get_viking_fs()
        for index, memory in enumerate(response.memories[:_TARGET_AGGREGATE_MEMORY_COUNT], start=1):
            title = self._clean_title(memory.title, index)
            filename = self._unique_filename(title, used_names)
            uri = f"{aggregate_dir_uri.rstrip('/')}/{filename}"
            resource_uris = self._valid_resource_uris(
                [*memory.resource_uris, *self._extract_resource_uris(memory.content)]
            )[:_MAX_REPRESENTATIVE_LINKS]
            content = self._truncate_text(memory.content.strip(), _MAX_VISIBLE_CONTENT_CHARS)
            if not content:
                continue
            item_count = self._memory_item_count(memory, input_item_count)
            mf = MemoryFile(
                uri=uri,
                content=content,
                memory_type=RESOURCE_LINK_MEMORY_TYPE,
                extra_fields={
                    "topic": title,
                    RESOURCE_LINK_STATE_FIELD: {
                        "item_count": item_count,
                        "updated_at": now,
                        "representative_resources": [
                            {"resource_uri": resource_uri} for resource_uri in resource_uris
                        ],
                    },
                    "resource_refs": [
                        {
                            "resource_uri": resource_uri,
                            "source": RESOURCE_LINK_COMPACTION_REF_SOURCE,
                            "created_at": now,
                            "match_text": title,
                        }
                        for resource_uri in resource_uris
                    ],
                },
            )
            await viking_fs.write_file(uri, MemoryFileUtils.write(mf), ctx=ctx)
            await MemoryUpdater.refresh_file_embedding(
                viking_fs=viking_fs,
                vikingdb=self._vikingdb,
                uri=uri,
                memory_type=RESOURCE_LINK_MEMORY_TYPE,
                ctx=ctx,
            )
            written_uris.append(uri)
        return written_uris

    async def _delete_compacted_inputs(
        self,
        *,
        ctx: RequestContext,
        candidates: Sequence[_CompactionCandidate],
        keep_uris: set[str],
    ) -> List[str]:
        deleted: List[str] = []
        viking_fs = self._get_viking_fs()
        for candidate in candidates:
            if candidate.uri in keep_uris:
                continue
            try:
                current_raw = await viking_fs.read_file(candidate.uri, ctx=ctx)
            except (NotFoundError, FileNotFoundError, KeyError):
                continue
            except Exception:
                continue
            if self._hash_raw(current_raw) != candidate.raw_hash:
                logger.info("Skip deleting changed resource-link memory: %s", candidate.uri)
                continue
            try:
                await viking_fs.rm(candidate.uri, recursive=False, ctx=ctx)
                deleted.append(candidate.uri)
            except (NotFoundError, FileNotFoundError, KeyError):
                continue
        return deleted

    async def _refresh_deleted_parent_overviews(
        self,
        *,
        ctx: RequestContext,
        deleted_uris: Sequence[str],
    ) -> None:
        viking_fs = self._get_viking_fs()
        parent_dirs = {
            uri.rsplit("/", 1)[0]
            for uri in deleted_uris
            if context_type_for_uri(uri) == "memory" and "/" in uri
        }
        for directory_uri in sorted(parent_dirs):
            await MemoryUpdater.refresh_schema_overview(
                viking_fs=viking_fs,
                directory_uri=directory_uri,
                ctx=ctx,
            )

    def _lock_path(self, aggregate_dir_uri: str, ctx: RequestContext) -> str:
        viking_fs = self._get_viking_fs()
        if hasattr(viking_fs, "_uri_to_path"):
            return viking_fs._uri_to_path(aggregate_dir_uri, ctx=ctx)
        return aggregate_dir_uri

    def _is_stale(self, key: str, version: int) -> bool:
        if not key or version <= 0:
            return False
        with self._coalesce_lock:
            return version < self._coalesce_versions.get(key, 0)

    @staticmethod
    def _coalesce_key(ctx: RequestContext) -> str:
        return f"{ctx.account_id}|{ctx.user.user_id}|{RESOURCE_LINK_MEMORY_TYPE}"

    @staticmethod
    def _unwrap_queue_data(data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not isinstance(data, dict):
            return data
        payload = data.get("data")
        if isinstance(payload, str):
            try:
                parsed = json.loads(payload)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                return data
        if isinstance(payload, dict):
            return payload
        return data

    @staticmethod
    def _hash_raw(raw: Any) -> str:
        if isinstance(raw, bytes):
            data = raw
        else:
            data = str(raw or "").encode("utf-8")
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def _is_hidden_memory_file(uri: str) -> bool:
        leaf = uri.rsplit("/", 1)[-1]
        return leaf.startswith(".") or uri.endswith("/.overview.md") or uri.endswith("/.abstract.md")

    @staticmethod
    def _coerce_resource_refs(value: Any) -> List[Dict[str, Any]]:
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            return [dict(value)]
        return []

    @classmethod
    def _resource_uris_from_memory(
        cls,
        mf: MemoryFile,
        refs: Sequence[Dict[str, Any]],
    ) -> List[str]:
        uris = [str(ref.get("resource_uri") or "") for ref in refs]
        uris.extend(cls._extract_resource_uris(mf.content or ""))
        return cls._valid_resource_uris(uris)

    @staticmethod
    def _valid_resource_uris(values: Sequence[str]) -> List[str]:
        result: List[str] = []
        for value in values:
            uri = str(value or "").strip().rstrip(".,;:!?，。；：！？")
            if uri.startswith("viking://resources/") and uri not in result:
                result.append(uri)
        return result

    @staticmethod
    def _extract_resource_uris(text: str) -> List[str]:
        return _RESOURCE_URI_RE.findall(text or "")

    @classmethod
    def _candidate_sort_key(cls, item: _CompactionCandidate) -> str:
        values = [item.extra_fields.get(RESOURCE_LINK_CREATED_AT_FIELD)]
        values.extend(ref.get("created_at") for ref in item.resource_refs)
        for value in values:
            if value:
                return str(value)
        return item.uri

    @classmethod
    def _single_prompt_item(cls, item: _CompactionCandidate) -> Dict[str, Any]:
        primary_ref = item.resource_refs[0] if item.resource_refs else {}
        return {
            "memory_uri": item.uri,
            "content": cls._truncate_text(item.content, _MAX_REASON_CHARS * 2),
            "reason": cls._truncate_text(str(primary_ref.get("reason") or ""), _MAX_REASON_CHARS),
            "created_at": primary_ref.get("created_at")
            or item.extra_fields.get(RESOURCE_LINK_CREATED_AT_FIELD)
            or "",
            "resource_uris": item.resource_uris[:_MAX_REPRESENTATIVE_LINKS],
            "item_count": 1,
        }

    @classmethod
    def _aggregate_prompt_item(cls, item: _CompactionCandidate) -> Dict[str, Any]:
        state = item.extra_fields.get(RESOURCE_LINK_STATE_FIELD)
        return {
            "memory_uri": item.uri,
            "topic": item.extra_fields.get("topic") or item.uri.rsplit("/", 1)[-1].removesuffix(".md"),
            "content": cls._truncate_text(item.content, _MAX_AGGREGATE_CONTENT_CHARS),
            "resource_uris": item.resource_uris[:_MAX_REPRESENTATIVE_LINKS],
            "item_count": item.item_count,
            "state": state if isinstance(state, dict) else {},
        }

    @staticmethod
    def _output_language(
        batch: Sequence[_CompactionCandidate],
        aggregates: Sequence[_CompactionCandidate],
    ) -> str:
        sample = "\n".join(
            item.content for item in [*list(batch[:5]), *list(aggregates[:3])] if item.content
        )
        return resolve_output_language(sample)

    @classmethod
    def _clean_title(cls, title: str, index: int) -> str:
        title = " ".join(str(title or "").split())
        title = _UNSAFE_FILENAME_CHARS_RE.sub("-", title).strip(" .-_")
        title = _TITLE_DATE_PREFIX_RE.sub("", title).strip(" .-_")
        title = _TITLE_USER_ACTION_PREFIX_RE.sub("", title).strip(" .-_")
        if not title:
            title = f"资源集合{index}"
        return title[:_MAX_TITLE_CHARS].strip(" .-_") or f"资源集合{index}"

    @staticmethod
    def _unique_filename(title: str, used_names: set[str]) -> str:
        base = title.removesuffix(".md")
        filename = f"{base}.md"
        suffix = 2
        while filename in used_names:
            filename = f"{base}-{suffix}.md"
            suffix += 1
        used_names.add(filename)
        return filename

    @staticmethod
    def _memory_item_count(memory: _CompactedMemory, fallback_total: int) -> int:
        try:
            count = int(memory.item_count)
        except (TypeError, ValueError):
            count = 0
        if count > 0:
            return count
        if fallback_total > 0 and len(memory.resource_uris) <= 1:
            return fallback_total
        return max(1, len(memory.resource_uris))

    @staticmethod
    def _truncate_text(text: Any, max_chars: int) -> str:
        value = " ".join(str(text or "").split())
        if len(value) <= max_chars:
            return value
        return value[: max_chars - 3].rstrip() + "..."

    @staticmethod
    def _safe_int(value: Any, *, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
