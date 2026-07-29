"""Base LLM provider interface."""

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Literal

import json_repair


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


@dataclass
class LLMStreamEvent:
    """Streaming event emitted by an LLM provider."""

    type: Literal["content_delta", "reasoning_delta", "response"]
    content: str | None = None
    response: LLMResponse | None = None


def stream_delta_value(delta: Any, name: str) -> str:
    value = getattr(delta, name, None)
    return value if isinstance(value, str) else ""


def merge_stream_tool_call_delta(
    raw_tool_calls: dict[int, dict[str, Any]],
    delta_tool_call: Any,
    fallback_index: int | None = None,
) -> None:
    index = getattr(delta_tool_call, "index", None)
    if index is None:
        index = fallback_index if fallback_index is not None else len(raw_tool_calls)
    entry = raw_tool_calls.setdefault(
        int(index),
        {"id": "", "name": "", "arguments": ""},
    )
    tool_call_id = getattr(delta_tool_call, "id", None)
    if tool_call_id:
        entry["id"] = tool_call_id
    function = getattr(delta_tool_call, "function", None)
    if function is None:
        return
    name = getattr(function, "name", None)
    if name:
        entry["name"] += name
    arguments = getattr(function, "arguments", None)
    if arguments:
        entry["arguments"] += arguments


def parse_tool_arguments(raw_arguments: Any) -> dict[str, Any]:
    """Parse provider tool arguments, repairing only complete JSON objects."""
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if not isinstance(raw_arguments, str) or not raw_arguments:
        return {}
    try:
        parsed = json.loads(raw_arguments)
    except json.JSONDecodeError:
        stripped = raw_arguments.strip()
        if not (stripped.startswith("{") and stripped.endswith("}")):
            return {"raw": raw_arguments}
        try:
            parsed = json_repair.loads(raw_arguments)
        except (ValueError, TypeError, RecursionError):
            return {"raw": raw_arguments}
        if not isinstance(parsed, dict):
            return {"raw": raw_arguments}
    return parsed if isinstance(parsed, dict) else {}


def build_stream_response(
    *,
    content: str,
    reasoning_content: str,
    raw_tool_calls: dict[int, dict[str, Any]],
    finish_reason: str,
    usage: dict[str, int] | None = None,
    token_counter: Callable[[str, str], int] | None = None,
) -> LLMResponse:
    tool_calls: list[ToolCallRequest] = []
    for index in sorted(raw_tool_calls):
        raw_tool_call = raw_tool_calls[index]
        name = str(raw_tool_call.get("name") or "")
        if not name:
            continue
        raw_arguments = str(raw_tool_call.get("arguments") or "")
        arguments = parse_tool_arguments(raw_arguments)
        tokens = token_counter(name, raw_arguments) if token_counter else 0
        tool_calls.append(
            ToolCallRequest(
                id=str(raw_tool_call.get("id") or f"tool_call_{index}"),
                name=name,
                arguments=arguments,
                tokens=tokens,
            )
        )

    return LLMResponse(
        content=content or None,
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        usage=usage or {},
        reasoning_content=reasoning_content or None,
    )


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

        Returns:
            LLMResponse with content and/or tool calls.
        """
        pass

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        session_id: str | None = None,
    ) -> AsyncIterator[LLMStreamEvent]:
        """Stream a chat completion request.

        Providers without native streaming fall back to a single final response event.
        """
        response = await self.chat(
            messages=messages,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            session_id=session_id,
        )
        yield LLMStreamEvent(type="response", response=response)

    @abstractmethod
    def get_default_model(self) -> str:
        """Get the default model for this provider."""
        pass
