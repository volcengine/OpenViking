# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tool-output externalization for OpenViking sessions.

Coding-agent tool calls can emit very large outputs. To keep the active
context bounded, a committed session offloads oversized tool outputs to a
``tool-results/`` content store and leaves a compact, deterministic preview in
the message. The preview keeps enough metadata (``ref``, ``sha256``,
``original_chars`` …) that the full content can be re-read on demand and
re-hydrated for memory extraction.

``ToolOutputExternalizer`` owns that whole path:

* ``externalize_group`` — pick which tool parts in one assistant Turn to
  externalize so the inline budget is respected, then rewrite each selected
  part in place.
* ``hydrate_for_extraction`` — return a memory-only copy with externalized
  outputs restored and inline images redacted.
* ``read_tool_result`` / ``search_tool_result`` / ``list_tool_results`` — the
  read-side API over the content store.

It holds only the filesystem handle, session identity, and request context;
the externalization config is passed per call so live config edits take effect.
"""

from typing import Any, Dict, List, Optional

from openviking.message import Message
from openviking.message.part import ToolPart
from openviking.server.config import ToolOutputExternalizationConfig
from openviking.session import working_memory as wm
from openviking.session.tool_result_store import (
    ToolResultStore,
    build_tool_result_id,
    make_preview,
    render_preview_from_synopsis,
    sha256_text,
)
from openviking.session.tool_result_synopsis import (
    ToolResultSynopsis,
    generate_tool_result_synopsis,
)
from openviking_cli.exceptions import FailedPreconditionError, NotFoundError
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class ToolOutputExternalizer:
    """Offload oversized tool outputs and restore them for extraction."""

    def __init__(self, viking_fs: Any, session_uri: str, session_id: str, ctx: Any):
        self._viking_fs = viking_fs
        self._session_uri = session_uri
        self._session_id = session_id
        self._ctx = ctx

    def tool_result_store(self) -> Optional[ToolResultStore]:
        if not self._viking_fs:
            return None
        return ToolResultStore(
            self._viking_fs,
            self._session_uri,
            self._session_id,
            self._ctx,
        )

    # ------------------------------------------------------------------
    # Read-side API
    # ------------------------------------------------------------------

    async def read_tool_result(
        self,
        tool_result_id: str,
        *,
        offset: int = 0,
        limit: int = 20_000,
        include_metadata: bool = True,
    ) -> Dict[str, Any]:
        store = self.tool_result_store()
        if not store:
            raise NotFoundError(tool_result_id, "tool result")
        return await store.read(
            tool_result_id,
            offset=offset,
            limit=limit,
            include_metadata=include_metadata,
        )

    async def search_tool_result(
        self,
        tool_result_id: str,
        *,
        query: str,
        limit: int = 20,
        context_chars: int = 300,
    ) -> Dict[str, Any]:
        store = self.tool_result_store()
        if not store:
            raise NotFoundError(tool_result_id, "tool result")
        return await store.search(
            tool_result_id,
            query=query,
            limit=limit,
            context_chars=context_chars,
        )

    async def list_tool_results(
        self,
        *,
        tool_name: Optional[str] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        store = self.tool_result_store()
        if not store:
            return {"tool_results": []}
        return await store.list(tool_name=tool_name, limit=limit)

    # ------------------------------------------------------------------
    # Hydration for memory extraction
    # ------------------------------------------------------------------

    async def hydrate_for_extraction(self, messages: List[Message]) -> List[Message]:
        """Return a sanitized memory-only copy with externalized tool outputs restored."""
        hydrated = [Message.from_dict(m.to_dict()) for m in messages]
        store = self.tool_result_store()
        if not store:
            return wm.redact_inline_images_from_tool_outputs(hydrated)

        for msg in hydrated:
            for part in msg.parts:
                if not isinstance(part, ToolPart):
                    continue
                if not part.tool_output_ref:
                    continue
                if not (part.tool_output_truncated or part.tool_output_source_ref):
                    continue

                ref = part.tool_output_source_ref or part.tool_output_ref
                tool_result_id = ref.rstrip("/").split("/")[-1]
                offset = part.tool_output_source_offset if part.tool_output_source_ref else 0
                limit = part.tool_output_source_limit if part.tool_output_source_ref else -1
                if (
                    part.tool_output_source_ref
                    and limit is None
                    and part.tool_output_original_chars is not None
                ):
                    limit = part.tool_output_original_chars
                try:
                    result = await store.read(
                        tool_result_id,
                        offset=max(0, int(offset or 0)),
                        limit=int(limit) if limit is not None else -1,
                        include_metadata=False,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to hydrate externalized tool output for extraction: "
                        "session=%s message_id=%s tool_id=%s ref=%s error=%s",
                        self._session_id,
                        msg.id,
                        part.tool_id,
                        ref,
                        exc,
                    )
                    continue
                part.tool_output = result.get("content", "")

        return wm.redact_inline_images_from_tool_outputs(hydrated)

    # ------------------------------------------------------------------
    # Externalization
    # ------------------------------------------------------------------

    @staticmethod
    def _effective_preview_chars(
        cfg: ToolOutputExternalizationConfig,
        externalized_count: int,
    ) -> int:
        if externalized_count <= 0:
            return cfg.preview_chars
        group_share = cfg.assistant_turn_preview_budget_chars // externalized_count
        return max(0, min(cfg.preview_chars, max(cfg.min_preview_chars, group_share)))

    def _rewrite_source_read_tool_output(
        self,
        part: ToolPart,
        cfg: ToolOutputExternalizationConfig,
        *,
        group_id: str,
        group_original_chars: int,
    ) -> bool:
        """Rewrite read-back tool output as a source reference, not a new result."""
        if part.tool_name != "openviking_tool_result_read":
            return False
        tool_input = part.tool_input if isinstance(part.tool_input, dict) else {}
        source_ref = str(
            tool_input.get("tool_output_ref")
            or tool_input.get("ref")
            or tool_input.get("uri")
            or ""
        )
        if not source_ref.startswith(f"{self._session_uri}/tool-results/"):
            return False

        output = part.tool_output or ""
        preview_chars = max(cfg.min_preview_chars, cfg.preview_chars)
        preview = make_preview(
            output,
            preview_chars=preview_chars,
            ref=source_ref,
            tool_name=part.tool_name,
            sha256=sha256_text(output) if output else "",
            reason="source_read",
            original_chars=len(output),
            mime_type=part.tool_output_mime_type or "text/plain",
        )
        part.tool_output = preview
        part.tool_output_ref = source_ref
        part.tool_output_truncated = len(output) > len(preview)
        part.tool_output_original_chars = len(output)
        part.tool_output_preview_chars = len(preview)
        part.tool_output_sha256 = sha256_text(output) if output else ""
        part.tool_output_storage_uri = source_ref
        part.tool_output_source_ref = source_ref
        part.tool_output_source_offset = tool_input.get("offset")
        part.tool_output_source_limit = tool_input.get("limit")
        part.tool_output_group_id = group_id
        part.tool_output_externalized_reason = "source_read"
        part.tool_output_group_original_chars = group_original_chars
        part.tool_output_group_budget_chars = cfg.assistant_turn_inline_budget_chars
        return True

    async def _externalize_tool_part(
        self,
        msg: Message,
        part: ToolPart,
        cfg: ToolOutputExternalizationConfig,
        *,
        preview_chars: int,
        reason: str,
        group_id: str,
        group_original_chars: int,
        synopsis: Optional[ToolResultSynopsis] = None,
    ) -> None:
        store = self.tool_result_store()
        original_output = part.tool_output or ""
        if not store or not original_output:
            return

        digest = sha256_text(original_output)
        try:
            stored = await store.write(
                content=original_output,
                tool_id=part.tool_id,
                tool_name=part.tool_name,
                message_id=msg.id,
                user_id=self._ctx.user.user_id if self._ctx and self._ctx.user else None,
                peer_id=msg.peer_id,
                created_at=msg.created_at,
                preview_chars=preview_chars,
                mime_type=part.tool_output_mime_type or "text/plain",
                synopsis=synopsis,
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            part.tool_output_externalization_error = error
            if cfg.failure_mode == "reject":
                raise FailedPreconditionError(
                    "Failed to externalize tool output",
                    details={"tool_id": part.tool_id, "error": error},
                ) from exc
            if cfg.failure_mode == "preview_only":
                part.tool_output = make_preview(
                    original_output,
                    preview_chars=preview_chars,
                    tool_name=part.tool_name,
                    sha256=digest,
                    reason=f"{reason}:externalization_failed",
                    original_chars=len(original_output),
                    mime_type=part.tool_output_mime_type or "text/plain",
                )
                part.tool_output_ref = ""
                part.tool_output_truncated = True
                part.tool_output_original_chars = len(original_output)
                part.tool_output_preview_chars = len(part.tool_output)
                part.tool_output_sha256 = digest
                part.tool_output_externalized_reason = reason
            return

        ref = stored.storage_uri
        part.tool_output = render_preview_from_synopsis(
            stored.synopsis,
            ref=ref,
            tool_name=part.tool_name,
            sha256=digest,
            reason=reason,
            original_chars=len(original_output),
            preview_chars=min(len(original_output), max(preview_chars, 0)),
        )
        part.tool_output_ref = ref
        part.tool_output_truncated = True
        part.tool_output_original_chars = len(original_output)
        part.tool_output_preview_chars = len(part.tool_output)
        part.tool_output_sha256 = digest
        part.tool_output_storage_uri = ref
        part.tool_output_mime_type = stored.metadata.get("mime_type", "text/plain")
        part.tool_output_group_id = group_id
        part.tool_output_externalized_reason = reason
        part.tool_output_group_original_chars = group_original_chars
        part.tool_output_group_budget_chars = cfg.assistant_turn_inline_budget_chars

    async def externalize_group(
        self,
        messages: List[Message],
        cfg: ToolOutputExternalizationConfig,
    ) -> None:
        if not cfg.enabled:
            return

        tool_parts = [
            (msg, p)
            for msg in messages
            for p in msg.parts
            if isinstance(p, ToolPart) and (p.tool_output or "")
        ]
        if not tool_parts:
            return

        group_id = messages[0].id
        group_original_chars = sum(
            (
                int(p.tool_output_original_chars)
                if p.tool_output_ref
                and p.tool_output_truncated
                and p.tool_output_original_chars is not None
                else len(p.tool_output or "")
            )
            for _, p in tool_parts
        )
        normal_indices: List[int] = []
        selected: set[int] = set()
        externalized_preview_cache: Dict[tuple[int, int, str], tuple[ToolResultSynopsis, int]] = {}

        for idx, (_msg, part) in enumerate(tool_parts):
            part.tool_output_group_id = group_id
            part.tool_output_group_original_chars = group_original_chars
            part.tool_output_group_budget_chars = cfg.assistant_turn_inline_budget_chars
            if self._rewrite_source_read_tool_output(
                part,
                cfg,
                group_id=group_id,
                group_original_chars=group_original_chars,
            ):
                continue
            if part.tool_output_ref and part.tool_output_truncated:
                continue
            normal_indices.append(idx)
            if len(part.tool_output or "") > cfg.threshold_chars:
                selected.add(idx)

        def prepared_externalized_preview(
            idx: int, part: ToolPart, preview_chars: int
        ) -> tuple[ToolResultSynopsis, int]:
            content = part.tool_output or ""
            reason = "single_threshold" if len(content) > cfg.threshold_chars else "turn_budget"
            cache_key = (idx, preview_chars, reason)
            cached = externalized_preview_cache.get(cache_key)
            if cached is not None:
                return cached

            synopsis = generate_tool_result_synopsis(
                content,
                preview_chars=preview_chars,
                tool_name=part.tool_name,
                mime_type=part.tool_output_mime_type or "text/plain",
            )
            digest = sha256_text(content)
            ref = f"{self._session_uri}/tool-results/{build_tool_result_id(part.tool_id, digest)}"
            rendered = render_preview_from_synopsis(
                synopsis,
                ref=ref,
                tool_name=part.tool_name,
                sha256=digest,
                reason=reason,
                original_chars=len(content),
                preview_chars=min(len(content), max(preview_chars, 0)),
            )
            prepared = (synopsis, len(rendered))
            externalized_preview_cache[cache_key] = prepared
            return prepared

        def projected_inline_chars(selected_indices: set[int]) -> int:
            preview_chars = self._effective_preview_chars(cfg, len(selected_indices))
            total = 0
            for idx, (_, part) in enumerate(tool_parts):
                output_len = len(part.tool_output or "")
                if idx in selected_indices:
                    _synopsis, rendered_len = prepared_externalized_preview(
                        idx, part, preview_chars
                    )
                    total += rendered_len
                else:
                    total += output_len
            return total

        remaining = sorted(
            [idx for idx in normal_indices if idx not in selected],
            key=lambda idx: len(tool_parts[idx][1].tool_output or ""),
            reverse=True,
        )
        while (
            projected_inline_chars(selected) >= cfg.assistant_turn_inline_budget_chars and remaining
        ):
            baseline = projected_inline_chars(selected)
            chosen_pos = None
            for pos, idx in enumerate(remaining):
                candidate = set(selected)
                candidate.add(idx)
                if projected_inline_chars(candidate) < baseline:
                    chosen_pos = pos
                    break
            if chosen_pos is None:
                break
            selected.add(remaining.pop(chosen_pos))

        preview_chars = self._effective_preview_chars(cfg, len(selected))
        for idx in sorted(selected):
            msg, part = tool_parts[idx]
            reason = (
                "single_threshold"
                if len(part.tool_output or "") > cfg.threshold_chars
                else "turn_budget"
            )
            synopsis, _rendered_len = prepared_externalized_preview(idx, part, preview_chars)
            await self._externalize_tool_part(
                msg,
                part,
                cfg,
                preview_chars=preview_chars,
                reason=reason,
                group_id=group_id,
                group_original_chars=group_original_chars,
                synopsis=synopsis,
            )
