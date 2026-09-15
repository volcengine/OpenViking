# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared contract for memory extraction output protocols."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from openviking.session.memory.dataclass import MemoryFile, MemoryTypeSchema


class ExtractionOutputProtocolError(ValueError):
    """A model response does not satisfy the selected output protocol."""

    def __init__(self, message: str, *, allow_tool_retry: bool = False) -> None:
        super().__init__(message)
        # True when the failure means "the model tried to read/search from the
        # write-only SDK"; the loop should re-open native tools for the retry.
        self.allow_tool_retry = allow_tool_retry


@dataclass(slots=True)
class ExtractionOutputContext:
    operations_model: type[BaseModel]
    schemas: tuple[MemoryTypeSchema, ...]
    page_id_map: Any
    read_file_contents: dict[str, MemoryFile]
    link_enabled: bool
    role_scope: Any | None = None
    available_tools: tuple[str, ...] = ()


class ExtractionOutputProtocol(ABC):
    """Render and parse one model-facing extraction output protocol."""

    name: str

    @abstractmethod
    def render_contract(self, context: ExtractionOutputContext) -> str:
        """Render the output API/schema shown to the model."""

    @abstractmethod
    def render_reference_rules(self, context: ExtractionOutputContext) -> str:
        """Render rules for referring to existing and newly created memories."""

    @abstractmethod
    def parse(
        self, content: str, context: ExtractionOutputContext
    ) -> tuple[Any | None, str | None]:
        """Parse model output into the shared dynamic operations model."""

    @abstractmethod
    def render_final_instruction(self, context: ExtractionOutputContext) -> str:
        """Render the instruction used when tool iterations are exhausted."""

    @abstractmethod
    def render_format_retry(self, error: str | None = None) -> str:
        """Render guidance after output parsing fails."""

    @abstractmethod
    def render_patch_repair(self, patch_errors: list[dict[str, Any]]) -> str:
        """Render guidance after a patch cannot be applied."""

    @abstractmethod
    def render_resolution_repair(self, issues: list[dict[str, Any]]) -> str:
        """Render guidance after event operations fail to resolve a write target.

        The loop preserves every already-successful operation and asks the model
        to re-emit only the corrected event operations, so the wording MUST match
        this protocol's own output shape (JSON object vs restricted Python SDK).
        """

    def render_new_bindings(self, context: ExtractionOutputContext, *, source: str) -> str:
        """Render newly available existing-memory references, if the protocol needs them."""
        del context, source
        return ""

    @abstractmethod
    def render_tool_result_messages(
        self,
        context: ExtractionOutputContext,
        *,
        call_id: str | int,
        tool_name: str,
        params: dict[str, Any],
        result: Any,
        source: str,
    ) -> list[dict[str, Any]]:
        """Render one completed tool invocation into model-facing context."""

    def render_prefetch_messages(
        self,
        messages: list[dict[str, Any]],
        context: ExtractionOutputContext,
    ) -> list[dict[str, Any]]:
        """Render provider-prefetched context in this protocol's model-facing form."""
        del context
        return list(messages)

    def normalize_provider_instruction(self, instruction: str) -> str:
        """Adapt provider-specific output wording to this protocol."""
        return instruction

    def describe_empty_response(self) -> str | None:
        """Return the parse-error text for an empty model response, if any."""
        return None

    def keep_tools_enabled_after_parse_error(self, error: str | None) -> bool:
        """Return whether native tools should remain available for the retry."""
        del error
        return False
