# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""CPU preparation for durable session messages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, List
from uuid import uuid4

from openviking.core.peer_id import normalize_peer_id
from openviking.message import Message
from openviking.message.part import ToolPart
from openviking.server.config import ToolOutputExternalizationConfig
from openviking.session.retention import build_turns
from openviking.session.tool_result_store import (
    PreparedToolResult,
    StoredToolResult,
    make_preview,
    prepare_tool_result,
    render_preview_from_synopsis,
    sha256_text,
)
from openviking_cli.exceptions import FailedPreconditionError, InvalidArgumentError


@dataclass
class ToolOutputWrite:
    message: Message
    part: ToolPart
    prepared: PreparedToolResult
    reason: str
    group_id: str
    group_original_chars: int
    group_budget_chars: int

    def apply_success(self, stored: StoredToolResult) -> None:
        content = self.prepared.content
        ref = stored.storage_uri
        self.part.tool_output = render_preview_from_synopsis(
            stored.synopsis,
            ref=ref,
            tool_name=self.part.tool_name,
            sha256=self.prepared.sha256,
            reason=self.reason,
            original_chars=len(content),
            preview_chars=min(len(content), max(self.prepared.preview_chars, 0)),
        )
        self.part.tool_output_ref = ref
        self.part.tool_output_truncated = True
        self.part.tool_output_original_chars = len(content)
        self.part.tool_output_preview_chars = len(self.part.tool_output)
        self.part.tool_output_sha256 = self.prepared.sha256
        self.part.tool_output_storage_uri = ref
        self.part.tool_output_mime_type = stored.metadata["mime_type"]
        self.part.tool_output_group_id = self.group_id
        self.part.tool_output_externalized_reason = self.reason
        self.part.tool_output_group_original_chars = self.group_original_chars
        self.part.tool_output_group_budget_chars = self.group_budget_chars

    def apply_failure(
        self,
        config: ToolOutputExternalizationConfig,
        exc: Exception,
    ) -> None:
        error = f"{type(exc).__name__}: {exc}"
        self.part.tool_output_externalization_error = error
        if config.failure_mode == "reject":
            raise FailedPreconditionError(
                "Failed to externalize tool output",
                details={"tool_id": self.part.tool_id, "error": error},
            ) from exc
        if config.failure_mode != "preview_only":
            return

        content = self.prepared.content
        self.part.tool_output = render_preview_from_synopsis(
            self.prepared.synopsis,
            tool_name=self.part.tool_name,
            sha256=self.prepared.sha256,
            reason=f"{self.reason}:externalization_failed",
            original_chars=len(content),
            preview_chars=min(len(content), max(self.prepared.preview_chars, 0)),
        )
        self.part.tool_output_ref = ""
        self.part.tool_output_truncated = True
        self.part.tool_output_original_chars = len(content)
        self.part.tool_output_preview_chars = len(self.part.tool_output)
        self.part.tool_output_sha256 = self.prepared.sha256
        self.part.tool_output_externalized_reason = self.reason


@dataclass
class PreparedMessageBatch:
    messages: List[Message]
    tool_outputs: List[ToolOutputWrite]

    def finalize_tokens(self) -> None:
        for message in self.messages:
            message.recalculate_tokens()


class MessagePreparer:
    """Own message normalization and expensive, pure preparation work."""

    def __init__(
        self,
        session_uri: str,
        config: ToolOutputExternalizationConfig,
    ) -> None:
        self._session_uri = session_uri
        self._config = config

    def prepare_new(self, messages_spec: List[dict]) -> PreparedMessageBatch:
        groups = [self._build_group(spec, index) for index, spec in enumerate(messages_spec)]
        return self.prepare_groups(groups)

    def prepare_turns(self, messages: List[Message]) -> PreparedMessageBatch:
        return self.prepare_groups(turn.messages for turn in build_turns(messages))

    def prepare_groups(
        self,
        groups: Iterable[List[Message]],
    ) -> PreparedMessageBatch:
        groups = list(groups)
        tool_outputs = []
        if self._config.enabled:
            for group in groups:
                tool_outputs.extend(self._prepare_tool_output_group(group))
        return PreparedMessageBatch(
            messages=[message for group in groups for message in group],
            tool_outputs=tool_outputs,
        )

    def _build_group(self, spec: dict, index: int) -> List[Message]:
        if "role" not in spec:
            raise ValueError(f"messages_spec[{index}]: missing required key 'role'")
        if "parts" not in spec:
            raise ValueError(f"messages_spec[{index}]: missing required key 'parts'")

        try:
            peer_id = normalize_peer_id(spec.get("peer_id"))
        except ValueError as exc:
            raise InvalidArgumentError(str(exc)) from exc

        role = spec["role"]
        parts = spec["parts"]
        created_at = spec.get("created_at") or datetime.now(timezone.utc).isoformat()
        common = {
            "role": role,
            "peer_id": peer_id,
            "created_at": created_at,
            "turn_id": spec.get("turn_id"),
            "source_message_ids": (
                list(spec["source_message_ids"])
                if spec.get("source_message_ids") is not None
                else None
            ),
        }
        if role == "user" and len(parts) > 1 and all(isinstance(part, ToolPart) for part in parts):
            return [
                Message(
                    id=f"msg_{uuid4().hex}",
                    parts=[part],
                    message_kind=spec.get("message_kind") or "tool_transport",
                    **common,
                )
                for part in parts
            ]
        return [
            Message(
                id=f"msg_{uuid4().hex}",
                parts=parts,
                message_kind=spec.get("message_kind"),
                **common,
            )
        ]

    def _effective_preview_chars(self, externalized_count: int) -> int:
        if externalized_count <= 0:
            return self._config.preview_chars
        group_share = self._config.assistant_turn_preview_budget_chars // externalized_count
        return max(
            0,
            min(
                self._config.preview_chars,
                max(self._config.min_preview_chars, group_share),
            ),
        )

    def _rewrite_source_read(
        self,
        part: ToolPart,
        *,
        group_id: str,
        group_original_chars: int,
    ) -> bool:
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
        digest = sha256_text(output) if output else ""
        preview = make_preview(
            output,
            preview_chars=max(self._config.min_preview_chars, self._config.preview_chars),
            ref=source_ref,
            tool_name=part.tool_name,
            sha256=digest,
            reason="source_read",
            original_chars=len(output),
            mime_type=part.tool_output_mime_type or "text/plain",
        )
        part.tool_output = preview
        part.tool_output_ref = source_ref
        part.tool_output_truncated = len(output) > len(preview)
        part.tool_output_original_chars = len(output)
        part.tool_output_preview_chars = len(preview)
        part.tool_output_sha256 = digest
        part.tool_output_storage_uri = source_ref
        part.tool_output_source_ref = source_ref
        part.tool_output_source_offset = tool_input.get("offset")
        part.tool_output_source_limit = tool_input.get("limit")
        part.tool_output_group_id = group_id
        part.tool_output_externalized_reason = "source_read"
        part.tool_output_group_original_chars = group_original_chars
        part.tool_output_group_budget_chars = self._config.assistant_turn_inline_budget_chars
        return True

    def _prepare_tool_output_group(self, messages: List[Message]) -> List[ToolOutputWrite]:
        tool_parts = [
            (message, part)
            for message in messages
            for part in message.parts
            if isinstance(part, ToolPart) and (part.tool_output or "")
        ]
        if not tool_parts:
            return []

        config = self._config
        group_id = messages[0].id
        group_original_chars = sum(
            int(part.tool_output_original_chars)
            if part.tool_output_ref
            and part.tool_output_truncated
            and part.tool_output_original_chars is not None
            else len(part.tool_output or "")
            for _, part in tool_parts
        )
        normal_indices: List[int] = []
        selected: set[int] = set()
        prepared_cache: dict[tuple[int, int], tuple[PreparedToolResult, int]] = {}

        for index, (_message, part) in enumerate(tool_parts):
            part.tool_output_group_id = group_id
            part.tool_output_group_original_chars = group_original_chars
            part.tool_output_group_budget_chars = config.assistant_turn_inline_budget_chars
            if self._rewrite_source_read(
                part,
                group_id=group_id,
                group_original_chars=group_original_chars,
            ):
                continue
            if part.tool_output_ref and part.tool_output_truncated:
                continue
            normal_indices.append(index)
            if len(part.tool_output or "") > config.threshold_chars:
                selected.add(index)

        def prepared_preview(
            index: int,
            part: ToolPart,
            preview_chars: int,
        ) -> tuple[PreparedToolResult, int]:
            content = part.tool_output or ""
            reason = "single_threshold" if len(content) > config.threshold_chars else "turn_budget"
            cache_key = (index, preview_chars)
            cached = prepared_cache.get(cache_key)
            if cached is not None:
                return cached
            prepared = prepare_tool_result(
                content,
                tool_id=part.tool_id,
                tool_name=part.tool_name,
                preview_chars=preview_chars,
                mime_type=part.tool_output_mime_type or "text/plain",
            )
            ref = f"{self._session_uri}/tool-results/{prepared.tool_result_id}"
            rendered = render_preview_from_synopsis(
                prepared.synopsis,
                ref=ref,
                tool_name=part.tool_name,
                sha256=prepared.sha256,
                reason=reason,
                original_chars=len(content),
                preview_chars=min(len(content), max(preview_chars, 0)),
            )
            result = (prepared, len(rendered))
            prepared_cache[cache_key] = result
            return result

        def projected_inline_chars(selected_indices: set[int]) -> int:
            preview_chars = self._effective_preview_chars(len(selected_indices))
            return sum(
                prepared_preview(index, part, preview_chars)[1]
                if index in selected_indices
                else len(part.tool_output or "")
                for index, (_message, part) in enumerate(tool_parts)
            )

        remaining = sorted(
            [index for index in normal_indices if index not in selected],
            key=lambda index: len(tool_parts[index][1].tool_output or ""),
            reverse=True,
        )
        while projected_inline_chars(selected) >= config.assistant_turn_inline_budget_chars:
            baseline = projected_inline_chars(selected)
            chosen = next(
                (
                    index
                    for index in remaining
                    if projected_inline_chars(selected | {index}) < baseline
                ),
                None,
            )
            if chosen is None:
                break
            selected.add(chosen)
            remaining.remove(chosen)

        preview_chars = self._effective_preview_chars(len(selected))
        writes = []
        for index in sorted(selected):
            message, part = tool_parts[index]
            reason = (
                "single_threshold"
                if len(part.tool_output or "") > config.threshold_chars
                else "turn_budget"
            )
            prepared, _rendered_len = prepared_preview(index, part, preview_chars)
            writes.append(
                ToolOutputWrite(
                    message=message,
                    part=part,
                    prepared=prepared,
                    reason=reason,
                    group_id=group_id,
                    group_original_chars=group_original_chars,
                    group_budget_chars=config.assistant_turn_inline_budget_chars,
                )
            )
        return writes
