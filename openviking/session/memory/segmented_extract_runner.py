# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Reusable segmented extraction runner.

Protects memory/session extraction against oversized model inputs by splitting
conversation messages into token-budgeted segments and executing them serially
within one provider phase.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from openviking.message import Message
from openviking.server.identity import RequestContext
from openviking.session.memory import ExtractLoop
from openviking.session.memory.core import (
    DEFAULT_CONTEXT_PROVIDER_RESERVE_TOKENS,
    ExtractContextProvider,
)
from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler
from openviking.session.memory.memory_updater import ExtractContext
from openviking.telemetry import tracer
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.config.memory_config import DEFAULT_MEMORY_INPUT_WINDOW_TOKENS

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ExtractionMessageSegment:
    """One contiguous extraction segment."""

    index: int
    messages: List[Message]
    start_index: int
    end_index: int
    estimated_tokens: int


@dataclass(slots=True)
class SegmentedExtractSharedContext:
    """Shared runtime objects reused across one provider's segmented run."""

    ctx: Optional[RequestContext]
    vlm: Any
    viking_fs: Any
    latest_archive_overview: str = ""
    transaction_handle: Any = None
    phase_label: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CallableProviderFactory:
    provider_cls: type[ExtractContextProvider]
    factory: Callable[[ExtractionMessageSegment, SegmentedExtractSharedContext], Any]

    def __call__(
        self,
        segment: ExtractionMessageSegment,
        shared_context: SegmentedExtractSharedContext,
    ) -> Any:
        return self.factory(segment, shared_context)

    def get_provider_cls(self) -> type[ExtractContextProvider]:
        return self.provider_cls


@dataclass(frozen=True, slots=True)
class CallableUpdaterFactory:
    updater_cls: type[Any]
    factory: Callable[[SegmentedExtractSharedContext], Any]

    def __call__(self, shared_context: SegmentedExtractSharedContext) -> Any:
        return self.factory(shared_context)

    def get_updater_cls(self) -> type[Any]:
        return self.updater_cls


class SegmentedExtractRunner:
    """Run one extraction provider serially over token-budgeted message segments."""

    def __init__(
        self,
        *,
        messages: List[Message],
        shared_context: SegmentedExtractSharedContext,
        provider_factory: CallableProviderFactory,
        updater_factory: CallableUpdaterFactory,
        input_window_tokens: Optional[int] = None,
    ):
        self._messages = list(messages or [])
        self._shared_context = shared_context
        self._provider_factory = provider_factory
        self._updater_factory = updater_factory
        self._input_window_tokens = self._coerce_positive_int(
            input_window_tokens,
            default=self._get_input_window_tokens(),
        )
        self._segments: Optional[List[ExtractionMessageSegment]] = None
        self.successful_operations: List[Any] = []

    @property
    def segments(self) -> List[ExtractionMessageSegment]:
        if self._segments is None:
            provider_cls = self._provider_factory.get_provider_cls()
            reserve_tokens = self._get_reserve_tokens(provider_cls)
            segment_budget = self._input_window_tokens - reserve_tokens
            self._segments = self.segment_messages(
                self._messages,
                max_segment_tokens=segment_budget,
            )
        return self._segments

    def _get_input_window_tokens(self) -> int:
        config = get_openviking_config()
        return self._coerce_positive_int(
            getattr(config.memory, "input_window_tokens", DEFAULT_MEMORY_INPUT_WINDOW_TOKENS),
            default=DEFAULT_MEMORY_INPUT_WINDOW_TOKENS,
        )

    @staticmethod
    def _coerce_positive_int(value: Any, *, default: int) -> int:
        if isinstance(value, bool):
            return default
        if isinstance(value, int):
            return value if value > 0 else default
        if isinstance(value, float) and value.is_integer() and value > 0:
            return int(value)
        if isinstance(value, str):
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                return default
            return parsed if parsed > 0 else default
        return default

    @classmethod
    def _get_reserve_tokens(cls, provider_cls: type[Any]) -> int:
        getter = getattr(provider_cls, "get_reserve_tokens", None)
        if not callable(getter):
            return DEFAULT_CONTEXT_PROVIDER_RESERVE_TOKENS
        return cls._coerce_positive_int(
            getter(),
            default=DEFAULT_CONTEXT_PROVIDER_RESERVE_TOKENS,
        )

    @staticmethod
    def _message_tokens(message: Message) -> int:
        try:
            return max(int(getattr(message, "estimated_tokens", 0) or 0), 0)
        except Exception:
            return 0

    @classmethod
    def segment_messages(
        cls,
        messages: List[Message],
        *,
        max_segment_tokens: int,
    ) -> List[ExtractionMessageSegment]:
        if not messages or max_segment_tokens <= 0:
            return []

        segments: List[ExtractionMessageSegment] = []
        current_messages: List[Message] = []
        current_tokens = 0
        current_start = 0

        def flush(end_index: int) -> None:
            nonlocal current_messages, current_tokens, current_start
            if not current_messages:
                return
            segments.append(
                ExtractionMessageSegment(
                    index=len(segments),
                    messages=list(current_messages),
                    start_index=current_start,
                    end_index=end_index,
                    estimated_tokens=current_tokens,
                )
            )
            current_messages = []
            current_tokens = 0

        for idx, message in enumerate(messages):
            msg_tokens = cls._message_tokens(message)

            if not current_messages:
                current_messages = [message]
                current_tokens = msg_tokens
                current_start = idx
                if msg_tokens > max_segment_tokens:
                    flush(idx)
                continue

            if current_tokens + msg_tokens > max_segment_tokens:
                flush(idx - 1)
                current_messages = [message]
                current_tokens = msg_tokens
                current_start = idx
                if msg_tokens > max_segment_tokens:
                    flush(idx)
                continue

            current_messages.append(message)
            current_tokens += msg_tokens

        flush(len(messages) - 1)
        return segments

    async def run(self) -> Any:
        provider_cls = self._provider_factory.get_provider_cls()
        updater_cls = self._updater_factory.get_updater_cls()
        empty_result = updater_cls.merge([])

        reserve_tokens = self._get_reserve_tokens(provider_cls)
        segment_budget = self._input_window_tokens - reserve_tokens
        phase_label = self._shared_context.phase_label or provider_cls.__name__

        if segment_budget <= 0:
            tracer.error(
                f"[{phase_label}] Skipping provider {provider_cls.__name__}: "
                f"input_window_tokens={self._input_window_tokens}, "
                f"reserve_tokens={reserve_tokens}"
            )
            return empty_result

        if not self._messages:
            return empty_result

        results = []
        for segment in self.segments:
            try:
                segment_result = await self._run_segment(segment)
            except Exception as exc:
                tracer.error(
                    f"[{phase_label}] Segment {segment.index} "
                    f"({segment.start_index}-{segment.end_index}) failed: {exc}"
                )
                logger.error(
                    "[%s] Segment %s (%s-%s) failed",
                    phase_label,
                    segment.index,
                    segment.start_index,
                    segment.end_index,
                    exc_info=True,
                )
                continue

            if segment_result is not None:
                results.append(segment_result)

        return updater_cls.merge(results)

    async def _run_segment(self, segment: ExtractionMessageSegment) -> Any:
        phase_label = self._shared_context.phase_label or self._provider_factory.get_provider_cls().__name__
        tracer.info(
            f"[{phase_label}] Running segment {segment.index} "
            f"({segment.start_index}-{segment.end_index}, tokens={segment.estimated_tokens})"
        )

        extract_context = ExtractContext(segment.messages)
        isolation_handler = MemoryIsolationHandler(self._shared_context.ctx, extract_context)
        isolation_handler.prepare_messages()

        provider = self._provider_factory(segment, self._shared_context)
        self._inject_provider_runtime(
            provider=provider,
            isolation_handler=isolation_handler,
        )

        extract_loop_cls = self._shared_context.metadata.get("extract_loop_cls", ExtractLoop)
        orchestrator = extract_loop_cls(
            vlm=self._shared_context.vlm,
            viking_fs=self._shared_context.viking_fs,
            ctx=self._shared_context.ctx,
            context_provider=provider,
            isolation_handler=isolation_handler,
        )
        orchestrator._transaction_handle = self._shared_context.transaction_handle
        operations, _ = await orchestrator.run()

        if operations is None:
            tracer.info(
                f"[{phase_label}] Segment {segment.index} produced no memory operations"
            )
            return None

        updater = self._updater_factory(self._shared_context)
        result = await self._apply_operations(
            updater=updater,
            operations=operations,
            provider=provider,
            extract_context=extract_context,
            isolation_handler=isolation_handler,
        )
        self.successful_operations.append(operations)
        return result

    def _inject_provider_runtime(
        self,
        *,
        provider: Any,
        isolation_handler: MemoryIsolationHandler,
    ) -> None:
        if hasattr(provider, "_isolation_handler"):
            provider._isolation_handler = isolation_handler
        if hasattr(provider, "_ctx"):
            provider._ctx = self._shared_context.ctx
        if hasattr(provider, "_viking_fs"):
            provider._viking_fs = self._shared_context.viking_fs
        if hasattr(provider, "_transaction_handle"):
            provider._transaction_handle = self._shared_context.transaction_handle
        if hasattr(provider, "set_transaction_handle"):
            provider.set_transaction_handle(self._shared_context.transaction_handle)

    async def _apply_operations(
        self,
        *,
        updater: Any,
        operations: Any,
        provider: Any,
        extract_context: ExtractContext,
        isolation_handler: MemoryIsolationHandler,
    ) -> Any:
        apply_fn = getattr(updater, "apply_operations")
        signature = inspect.signature(apply_fn)
        kwargs = {}

        if "extract_context" in signature.parameters:
            kwargs["extract_context"] = extract_context
        if "isolation_handler" in signature.parameters:
            kwargs["isolation_handler"] = isolation_handler
        if "provider" in signature.parameters:
            kwargs["provider"] = provider
        if "shared_context" in signature.parameters:
            kwargs["shared_context"] = self._shared_context

        return await apply_fn(operations, self._shared_context.ctx, **kwargs)
