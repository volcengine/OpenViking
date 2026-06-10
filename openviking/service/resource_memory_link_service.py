# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Link resource addition reasons to user memories.

This module keeps resource files immutable: all traceability lives in memory
files' MEMORY_FIELDS metadata.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

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
from openviking.session.memory.utils.link_renderer import LinkRenderer
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.storage import VikingDBManager
from openviking.storage.viking_fs import VikingFS, get_viking_fs
from openviking_cli.exceptions import NotFoundError
from openviking_cli.utils import VikingURI, get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)

RESOURCE_REF_SOURCE = "add_resource.reason"


@dataclass
class _MemoryRefMatch:
    memory_uri: str
    memory_file: MemoryFile
    resource_ref: Dict[str, Any]


class _ResourceLinkingProvider(SessionExtractContextProvider):
    """Provider for creating/updating memory from an add-resource reason."""

    def __init__(
        self,
        *,
        resource_uri: str,
        reason: str,
        source_name: Optional[str],
        **kwargs: Any,
    ):
        self.resource_uri = resource_uri
        self.reason = reason
        self.source_name = source_name or ""
        messages = [
            Message(
                id="resource-linking",
                role="user",
                parts=[
                    TextPart(
                        text=(
                            "Resource URI: "
                            f"{resource_uri}\nReason: {reason}\nSource name: {self.source_name}"
                        )
                    )
                ],
            )
        ]
        super().__init__(messages=messages, **kwargs)

    def instruction(self) -> str:
        return render_prompt(
            "processing.resource_linking",
            {
                "output_language": self.get_output_language(),
                "resource_uri": self.resource_uri,
                "reason": self.reason,
                "source_name": self.source_name,
            },
        )

    def _build_conversation_message(self) -> Dict[str, Any]:
        return {
            "role": "user",
            "content": (
                "## Resource Addition\n"
                f"Resource URI: {self.resource_uri}\n"
                f"Reason: {self.reason}\n"
                f"Source name: {self.source_name or 'N/A'}\n\n"
                "Analyze only this resource addition record and output all memory "
                "write/edit/delete operations in a single JSON response."
            ),
        }

    def _build_prefetch_search_query(self) -> str:
        return "\n".join(part for part in [self.reason, self.source_name] if part).strip()

    def get_conversation_text(self) -> str:
        return f"{self.reason}\n{self.resource_uri}\n{self.source_name}".strip()

    def _detect_language(self) -> str:
        from openviking.session.memory.utils import resolve_output_language

        return resolve_output_language(
            "\n".join(part for part in [self.reason, self.source_name] if part).strip()
        )


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
    ):
        self._vikingdb = vikingdb
        self._viking_fs = viking_fs

    def set_dependencies(
        self,
        *,
        vikingdb: Optional[VikingDBManager],
        viking_fs: VikingFS,
    ) -> None:
        self._vikingdb = vikingdb
        self._viking_fs = viking_fs

    def _get_viking_fs(self) -> VikingFS:
        return self._viking_fs or get_viking_fs()

    async def on_resource_added(
        self,
        *,
        ctx: RequestContext,
        resource_uri: str,
        reason: str,
        source_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Extract user memory from an add-resource reason."""
        reason = (reason or "").strip()
        if not reason:
            return {"status": "skipped", "reason": "empty_reason"}
        if not resource_uri:
            return {"status": "skipped", "reason": "empty_resource_uri"}

        provider = _ResourceLinkingProvider(
            resource_uri=resource_uri,
            reason=reason,
            source_name=source_name,
            ctx=ctx,
            viking_fs=self._get_viking_fs(),
        )
        operations, extract_context, isolation_handler = await self._run_extract_loop(
            provider=provider,
            ctx=ctx,
        )
        if not operations or not (
            operations.upsert_operations or operations.delete_file_contents or operations.errors
        ):
            return {"status": "no_changes", "memory_uris": []}

        result = await self._apply_memory_operations(
            provider=provider,
            operations=operations,
            ctx=ctx,
            extract_context=extract_context,
            isolation_handler=isolation_handler,
        )
        changed_uris = list(dict.fromkeys(result.written_uris + result.edited_uris))
        await self._append_resource_refs(
            memory_uris=changed_uris,
            resource_uri=resource_uri,
            reason=reason,
            ctx=ctx,
        )
        missing_uri = await self._memory_files_missing_resource_uri(changed_uris, resource_uri, ctx)
        return {
            "status": "success" if not result.errors else "partial_success",
            "memory_uris": changed_uris,
            "deleted_memory_uris": result.deleted_uris,
            "errors": [f"{uri}: {exc}" for uri, exc in result.errors],
            "missing_resource_uri_uris": missing_uri,
        }

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

    async def _append_resource_refs(
        self,
        *,
        memory_uris: Sequence[str],
        resource_uri: str,
        reason: str,
        ctx: RequestContext,
    ) -> None:
        viking_fs = self._get_viking_fs()
        created_at = datetime.now(timezone.utc).isoformat()
        for memory_uri in dict.fromkeys(memory_uris):
            if context_type_for_uri(memory_uri) != "memory":
                continue
            try:
                raw = await viking_fs.read_file(memory_uri, ctx=ctx)
                mf = MemoryFileUtils.read(raw, uri=memory_uri)
            except Exception as exc:
                logger.warning("Failed to read memory for resource ref append: %s", exc)
                continue
            existing_refs = self._coerce_resource_refs(mf.extra_fields.get("resource_refs"))
            allow_sentence_fallback = not any(
                not self._resource_ref_matches(ref.get("resource_uri"), resource_uri, recursive=False)
                for ref in existing_refs
            )
            match_text = self._pick_match_text(mf, reason)
            mf.content, rendered_match_text = self._link_resource_in_content(
                mf.content,
                resource_uri=resource_uri,
                match_text=match_text,
                allow_sentence_fallback=allow_sentence_fallback,
            )
            match_text = rendered_match_text or match_text
            ref = {
                "resource_uri": resource_uri,
                "reason": reason,
                "source": RESOURCE_REF_SOURCE,
                "created_at": created_at,
            }
            if match_text:
                ref["match_text"] = match_text
            mf.extra_fields["resource_refs"] = self._merge_resource_refs(
                existing_refs,
                ref,
            )
            await viking_fs.write_file(memory_uri, MemoryFileUtils.write(mf), ctx=ctx)

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

    async def _memory_files_missing_resource_uri(
        self,
        memory_uris: Iterable[str],
        resource_uri: str,
        ctx: RequestContext,
    ) -> List[str]:
        missing: List[str] = []
        viking_fs = self._get_viking_fs()
        for uri in memory_uris:
            try:
                raw = await viking_fs.read_file(uri, ctx=ctx)
            except Exception:
                continue
            if resource_uri not in raw:
                missing.append(uri)
        return missing

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
    def _merge_resource_refs(existing: Any, new_ref: Dict[str, Any]) -> List[Dict[str, Any]]:
        refs = ResourceMemoryLinkService._coerce_resource_refs(existing)
        for ref in refs:
            if (
                ref.get("resource_uri") == new_ref.get("resource_uri")
                and ref.get("source") == new_ref.get("source")
            ):
                ref.update({k: v for k, v in new_ref.items() if v})
                return refs
        refs.append(new_ref)
        return refs

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

    @classmethod
    def _pick_match_text(cls, memory_file: MemoryFile, reason: str) -> Optional[str]:
        content = memory_file.content or ""
        candidates = []
        name = str(memory_file.extra_fields.get("name") or "").strip()
        if name:
            candidates.append(name)
        reason_anchor = cls._extract_anchor_from_reason(reason)
        if reason_anchor:
            candidates.append(reason_anchor)
        for token in (reason or "").replace("，", " ").replace(",", " ").split():
            stripped = token.strip()
            if stripped:
                candidates.append(stripped)
        candidates.extend(["资源", "resource", "Resource"])
        for candidate in dict.fromkeys(candidates):
            if candidate and LinkRenderer._find_match_span(content, candidate):
                return candidate
        return None

    @staticmethod
    def _extract_anchor_from_reason(reason: str) -> Optional[str]:
        text = (reason or "").strip()
        if not text:
            return None
        patterns = [
            r"^(?:这是一张|这是|这张|这个|用户上传了(?:一张|一个)?|上传了(?:一张|一个)?|新增了|添加了)?\s*(?P<anchor>[^，,。！？\n]{1,60}?)(?:的)?(?:照片|图片|截图|图像|文件|资源|文档|身份证|证件|资料)\s*$",
            r"(?P<anchor>[^，,。！？\n]{1,60}?)(?:的)?(?:照片|图片|截图|图像|文件|资源|文档|身份证|证件|资料)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            anchor = (match.group("anchor") or "").strip()
            anchor = re.sub(r"^(?:关于|有关|一张|一个)\s*", "", anchor).strip()
            anchor = re.sub(r"(?:的|之)$", "", anchor).strip()
            if anchor:
                return anchor
        return None

    @classmethod
    def _link_resource_in_content(
        cls,
        content: str,
        *,
        resource_uri: str,
        match_text: Optional[str],
        allow_sentence_fallback: bool,
    ) -> tuple[str, Optional[str]]:
        content = content or ""
        if not content or not resource_uri:
            return content, None
        if cls._content_links_resource(content, resource_uri):
            return content, match_text

        if match_text:
            span = cls._find_unlinked_match_span(content, match_text)
            if span:
                linked = cls._replace_span_with_link(content, span, resource_uri)
                linked = cls._remove_redundant_visible_resource_uri(linked, resource_uri)
                return linked, content[span[0] : span[1]]

        if allow_sentence_fallback:
            span = cls._first_sentence_span(content, resource_uri)
            if span:
                linked = cls._replace_span_with_link(content, span, resource_uri)
                linked = cls._remove_redundant_visible_resource_uri(linked, resource_uri)
                return linked, content[span[0] : span[1]].strip()

        return content, match_text

    @staticmethod
    def _content_links_resource(content: str, resource_uri: str) -> bool:
        return bool(
            re.search(
                r"\[[^\]]+\]\(" + re.escape(resource_uri) + r"\)",
                content or "",
            )
        )

    @classmethod
    def _find_unlinked_match_span(
        cls,
        content: str,
        match_text: str,
    ) -> Optional[tuple[int, int]]:
        span = LinkRenderer._find_match_span(content, match_text)
        if not span:
            return None
        if cls._span_inside_markdown_link(content, span):
            return None
        return span

    @staticmethod
    def _span_inside_markdown_link(content: str, span: tuple[int, int]) -> bool:
        start, end = span
        for match in re.finditer(r"\[[^\]]+\]\([^)]+\)", content or ""):
            if start >= match.start() and end <= match.end():
                return True
        return False

    @staticmethod
    def _replace_span_with_link(
        content: str,
        span: tuple[int, int],
        resource_uri: str,
    ) -> str:
        start, end = span
        anchor = content[start:end]
        return f"{content[:start]}[{anchor}]({resource_uri}){content[end:]}"

    @staticmethod
    def _first_sentence_span(content: str, resource_uri: str) -> Optional[tuple[int, int]]:
        match = re.search(r"\S", content or "")
        if not match:
            return None
        start = match.start()
        line_end = content.find("\n", start)
        if line_end == -1:
            line_end = len(content)
        line = content[start:line_end]
        punctuation = re.search(r"[。！？.!?]", line)
        end = start + punctuation.end() if punctuation else line_end
        sentence = content[start:end].strip()
        if not sentence or resource_uri in sentence or len(sentence) > 160:
            return None
        if ResourceMemoryLinkService._span_inside_markdown_link(content, (start, end)):
            return None
        return start, end

    @staticmethod
    def _remove_redundant_visible_resource_uri(content: str, resource_uri: str) -> str:
        if not ResourceMemoryLinkService._content_links_resource(content, resource_uri):
            return content
        uri = ResourceMemoryLinkService._visible_resource_uri_pattern(resource_uri)
        label = r"(?:resource\s+URI|资源\s*URI|资源地址|资源链接)"
        patterns = [
            re.compile(rf"(?im)^[ \t]*(?:[-*]\s*)?{label}\s*[:：]\s*{uri}[ \t]*(?:\r?\n|$)"),
            re.compile(rf"\s*[,，;；]?\s*{label}\s*[:：]\s*{uri}"),
            re.compile(rf"\s*[:：]\s*{uri}"),
        ]
        cleaned = content
        for pattern in patterns:
            cleaned = pattern.sub("", cleaned)
        cleaned = re.sub(r"[ \t]+([。！？.!?,，;；])", r"\1", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    @staticmethod
    def _visible_resource_uri_pattern(resource_uri: str) -> str:
        markdown_escaped_chars = set(r"\`*_{}[]()#+-.!|")
        return "".join(
            rf"\\?{re.escape(char)}" if char in markdown_escaped_chars else re.escape(char)
            for char in resource_uri
        )

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
