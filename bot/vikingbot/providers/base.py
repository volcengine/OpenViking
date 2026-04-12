"""Base LLM provider interface."""

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable

from loguru import logger

from vikingbot.utils.helpers import cal_str_tokens

DeltaCallback = Callable[[str], Awaitable[None]]


@dataclass
class ToolCallRequest:
    """A tool call request from the LLM."""

    id: str
    name: str
    arguments: dict[str, Any]
    tokens: int


@dataclass
class LLMResponse:
    """Response from an LLM provider."""

    content: str | None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, int] = field(default_factory=dict)
    reasoning_content: str | None = None  # Kimi, DeepSeek-R1 etc.

    @property
    def has_tool_calls(self) -> bool:
        """Check if response contains tool calls."""
        return len(self.tool_calls) > 0


class LLMProvider(ABC):
    """
    Abstract base class for LLM providers.

    Implementations should handle the specifics of each provider's API
    while maintaining a consistent interface.
    """

    def __init__(self, api_key: str | None = None, api_base: str | None = None):
        self.api_key = api_key
        self.api_base = api_base

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        session_id: str | None = None,
        on_content_delta: DeltaCallback | None = None,
        on_reasoning_delta: DeltaCallback | None = None,
    ) -> LLMResponse:
        """
        Send a chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            tools: Optional list of tool definitions.
            model: Model identifier (provider-specific).
            max_tokens: Maximum tokens in response.
            temperature: Sampling temperature.
            session_id: Optional session ID for tracing.
            on_content_delta: Optional async callback invoked with each incremental
                content chunk. When provided, the provider streams from the LLM.
            on_reasoning_delta: Optional async callback invoked with each incremental
                reasoning chunk (for reasoning-capable models).

        Returns:
            LLMResponse with accumulated content and/or tool calls.
        """
        pass

    @abstractmethod
    def get_default_model(self) -> str:
        """Get the default model for this provider."""
        pass


def extract_usage(usage_obj: Any) -> dict[str, int]:
    """Extract usage dict from an OpenAI-shape usage object (streaming or not)."""
    usage: dict[str, int] = {
        "prompt_tokens": getattr(usage_obj, "prompt_tokens", 0) or 0,
        "completion_tokens": getattr(usage_obj, "completion_tokens", 0) or 0,
        "total_tokens": getattr(usage_obj, "total_tokens", 0) or 0,
    }
    details = getattr(usage_obj, "prompt_tokens_details", None)
    if details is not None:
        cached = getattr(details, "cached_tokens", None)
        if cached:
            usage["cache_read_input_tokens"] = cached
    elif hasattr(usage_obj, "cache_read_input_tokens"):
        cached = getattr(usage_obj, "cache_read_input_tokens", None)
        if cached:
            usage["cache_read_input_tokens"] = cached
    return usage


async def consume_stream(
    stream_iter: AsyncIterator[Any],
    on_content_delta: DeltaCallback | None,
    on_reasoning_delta: DeltaCallback | None,
) -> LLMResponse:
    """
    Consume an OpenAI-shape streaming response (LiteLLM or OpenAI SDK), forwarding
    deltas to callbacks and accumulating the full content / tool_calls / usage
    into a single LLMResponse.
    """
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    # Tool calls arrive in chunks keyed by index; accumulate per index.
    tool_call_acc: dict[int, dict[str, Any]] = {}
    finish_reason: str = "stop"
    usage: dict[str, int] = {}

    async for chunk in stream_iter:
        chunk_usage = getattr(chunk, "usage", None)
        if chunk_usage:
            usage = extract_usage(chunk_usage)

        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue
        choice = choices[0]

        if getattr(choice, "finish_reason", None):
            finish_reason = choice.finish_reason

        delta = getattr(choice, "delta", None)
        if delta is None:
            continue

        content_piece = getattr(delta, "content", None)
        if content_piece:
            content_parts.append(content_piece)
            if on_content_delta is not None:
                try:
                    await on_content_delta(content_piece)
                except Exception as cb_err:
                    logger.debug(f"on_content_delta callback failed: {cb_err}")

        reasoning_piece = getattr(delta, "reasoning_content", None)
        if reasoning_piece:
            reasoning_parts.append(reasoning_piece)
            if on_reasoning_delta is not None:
                try:
                    await on_reasoning_delta(reasoning_piece)
                except Exception as cb_err:
                    logger.debug(f"on_reasoning_delta callback failed: {cb_err}")

        delta_tool_calls = getattr(delta, "tool_calls", None)
        if delta_tool_calls:
            for dtc in delta_tool_calls:
                idx = getattr(dtc, "index", 0) or 0
                slot = tool_call_acc.setdefault(
                    idx, {"id": None, "name": None, "arguments": ""}
                )
                if getattr(dtc, "id", None):
                    slot["id"] = dtc.id
                fn = getattr(dtc, "function", None)
                if fn is not None:
                    if getattr(fn, "name", None):
                        slot["name"] = fn.name
                    args_piece = getattr(fn, "arguments", None)
                    if args_piece:
                        slot["arguments"] += args_piece

    tool_calls: list[ToolCallRequest] = []
    for idx in sorted(tool_call_acc.keys()):
        slot = tool_call_acc[idx]
        name = slot.get("name") or ""
        args_str = slot.get("arguments") or ""
        tokens = cal_str_tokens(name, text_type="en")
        if args_str:
            tokens += cal_str_tokens(args_str, text_type="mixed")
        try:
            args = json.loads(args_str) if args_str else {}
        except json.JSONDecodeError:
            args = {"raw": args_str}
        if not isinstance(args, dict):
            args = {"raw": args_str}
        tool_calls.append(
            ToolCallRequest(
                id=slot.get("id") or f"tool_{idx}",
                name=name,
                arguments=args,
                tokens=tokens,
            )
        )

    return LLMResponse(
        content="".join(content_parts) or None,
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        usage=usage,
        reasoning_content="".join(reasoning_parts) or None,
    )
