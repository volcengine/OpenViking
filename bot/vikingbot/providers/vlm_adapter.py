"""VLM-to-LLMProvider adapter.

Wraps openviking VLM backends (VLMBase) to implement the vikingbot
LLMProvider interface, so that bot.agents.provider / bot.agents.model
configuration semantics are consistent with openviking server's vlm section.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from loguru import logger

from openviking.utils.model_retry import is_retryable_rate_limit_error, rate_limit_retry_delay
from openviking.utils.multimodal import redact_image_data_urls
from vikingbot.integrations.langfuse import LangfuseClient
from vikingbot.providers.base import (
    LLMProvider,
    LLMResponse,
    LLMStreamEvent,
    ToolCallRequest,
    build_stream_response,
    merge_stream_tool_call_delta,
    stream_delta_value,
)
from vikingbot.utils.tracing import get_current_response_id


class VLMProviderAdapter(LLMProvider):
    """Adapter that wraps an openviking VLMBase instance as an LLMProvider.

    When bot.agents.provider is explicitly set, _make_provider() creates the
    appropriate VLM backend via VLMFactory.create() and wraps it with this
    adapter.  The VLM backend handles model name resolution internally (e.g.
    VolcEngineVLM passes the model verbatim, LiteLLMVLMProvider auto-detects
    the provider from model name keywords), so no manual prefixing is needed.
    """

    def __init__(
        self,
        vlm_instance,  # VLMBase
        default_model: str,
        langfuse_client: LangfuseClient | None = None,
    ):
        super().__init__(api_key=None, api_base=None)
        self._vlm = vlm_instance
        self._default_model = default_model
        self._langfuse = langfuse_client or LangfuseClient.get_instance()

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.7,
        session_id: str | None = None,
    ) -> LLMResponse:
        effective_model = model or self._default_model

        # --- Langfuse: start observation ---
        langfuse_observation = None
        try:
            if self._langfuse.enabled and self._langfuse._client:
                metadata: dict[str, Any] = {"has_tools": tools is not None}
                response_id = get_current_response_id()
                if response_id:
                    metadata["response_id"] = response_id
                client = self._langfuse._client
                if hasattr(client, "start_observation"):
                    langfuse_observation = client.start_observation(
                        name="llm-chat",
                        as_type="generation",
                        model=effective_model,
                        input=redact_image_data_urls(messages),
                        metadata=metadata,
                    )
                    if response_id:
                        self._langfuse.register_generation(
                            response_id, langfuse_observation, metadata=metadata
                        )

            # --- Call VLM backend ---
            attempt = 1
            # An explicit empty list asks VLM backends for a structured response
            # without exposing tools, so per-response usage is preserved.
            response_tools = tools if tools is not None else []
            while True:
                try:
                    result = await self._vlm.get_completion_async(
                        messages=messages,
                        thinking=getattr(self._vlm, "thinking", None),
                        tools=response_tools,
                        tool_choice="auto" if response_tools else None,
                    )
                    break
                except Exception as e:
                    if not is_retryable_rate_limit_error(e):
                        raise
                    delay = rate_limit_retry_delay(attempt)
                    logger.warning(
                        "VLM adapter chat rate limited; retrying attempt={} delay={:.1f}s error={}",
                        attempt,
                        delay,
                        e,
                    )
                    await asyncio.sleep(delay)
                    attempt += 1

            llm_response = self._convert_response(result)

            # --- Langfuse: end observation ---
            if langfuse_observation:
                self._end_langfuse_observation(langfuse_observation, llm_response)

            return llm_response

        except Exception as e:
            if langfuse_observation:
                self._end_langfuse_observation_error(langfuse_observation, e)
            return LLMResponse(
                content=f"Error calling LLM in VLM Adapter: {str(e)}",
                finish_reason="error",
            )

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.7,
        session_id: str | None = None,
    ) -> AsyncIterator[LLMStreamEvent]:
        if not self._supports_native_stream(self._vlm):
            async for event in super().chat_stream(
                messages=messages,
                tools=tools,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                session_id=session_id,
            ):
                yield event
            return

        try:
            stream_with_failover = getattr(self._vlm, "stream_with_failover", None)
            if callable(stream_with_failover):
                async for event in self._chat_stream_failover_with_retry(
                    stream_with_failover,
                    messages=messages,
                    tools=tools,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                ):
                    yield event
                return

            async for event in self._chat_stream_with_retry(
                self._vlm,
                messages=messages,
                tools=tools,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
            ):
                yield event
        except Exception as exc:
            yield LLMStreamEvent(
                type="response",
                response=LLMResponse(
                    content=f"Error calling LLM in VLM Adapter stream: {str(exc)}",
                    finish_reason="error",
                ),
            )

    @classmethod
    def _supports_native_stream(cls, vlm: Any) -> bool:
        instances = getattr(vlm, "_vlm_instances", None)
        if instances is not None:
            return bool(instances) and all(cls._supports_native_stream(item) for item in instances)

        primary = getattr(vlm, "primary", None)
        backup = getattr(vlm, "backup", None)
        if primary is not None and backup is not None:
            return cls._supports_native_stream(primary) and cls._supports_native_stream(backup)

        return getattr(vlm, "provider", None) in {"openai", "volcengine"} and callable(
            getattr(vlm, "get_async_client", None)
        )

    async def _chat_stream_failover_with_retry(
        self,
        stream_with_failover: Any,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int | None,
        temperature: float,
    ) -> AsyncIterator[LLMStreamEvent]:
        attempt = 1
        while True:
            emitted = False
            try:
                async for event in stream_with_failover(
                    lambda vlm: self._chat_stream_backend(
                        vlm,
                        messages=messages,
                        tools=tools,
                        model=model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )
                ):
                    emitted = True
                    yield event
                return
            except Exception as exc:
                if emitted or not is_retryable_rate_limit_error(exc):
                    raise
                delay = rate_limit_retry_delay(attempt)
                logger.warning(
                    "VLM credential stream rate limited; retrying attempt={} "
                    "delay={:.1f}s error={}",
                    attempt,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
                attempt += 1

    async def _chat_stream_with_retry(
        self,
        vlm: Any,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int | None,
        temperature: float,
    ) -> AsyncIterator[LLMStreamEvent]:
        attempt = 1
        while True:
            emitted = False
            try:
                async for event in self._chat_stream_backend(
                    vlm,
                    messages=messages,
                    tools=tools,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                ):
                    emitted = True
                    yield event
                return
            except Exception as exc:
                if emitted or not is_retryable_rate_limit_error(exc):
                    raise
                delay = rate_limit_retry_delay(attempt)
                logger.warning(
                    "VLM adapter stream rate limited; retrying attempt={} delay={:.1f}s error={}",
                    attempt,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
                attempt += 1

    async def _chat_stream_backend(
        self,
        vlm: Any,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int | None,
        temperature: float,
    ) -> AsyncIterator[LLMStreamEvent]:
        kwargs = self._build_stream_kwargs(
            vlm,
            messages=messages,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        finish_reason = "stop"
        usage: dict[str, int] = {}
        start_time = time.perf_counter()

        client = vlm.get_async_client()
        response = await client.chat.completions.create(**kwargs)
        async for chunk in response:
            chunk_usage = self._parse_usage(getattr(chunk, "usage", None))
            if chunk_usage:
                usage = chunk_usage

            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            choice = choices[0]
            if getattr(choice, "finish_reason", None):
                finish_reason = choice.finish_reason or finish_reason
            delta = getattr(choice, "delta", None)
            if delta is None:
                continue

            reasoning_delta = stream_delta_value(delta, "reasoning_content")
            if reasoning_delta:
                reasoning_parts.append(reasoning_delta)
                yield LLMStreamEvent(type="reasoning_delta", content=reasoning_delta)

            content_delta = stream_delta_value(delta, "content")
            if content_delta:
                content_parts.append(content_delta)
                yield LLMStreamEvent(type="content_delta", content=content_delta)

            for fallback_index, delta_tool_call in enumerate(
                getattr(delta, "tool_calls", None) or []
            ):
                merge_stream_tool_call_delta(
                    tool_calls,
                    delta_tool_call,
                    fallback_index=fallback_index,
                )

        if usage:
            self._record_vlm_usage(vlm, usage, time.perf_counter() - start_time)

        yield LLMStreamEvent(
            type="response",
            response=build_stream_response(
                content="".join(content_parts),
                reasoning_content="".join(reasoning_parts),
                raw_tool_calls=tool_calls,
                finish_reason=finish_reason,
                usage=usage,
            ),
        )

    def _build_stream_kwargs(
        self,
        vlm: Any,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int | None,
        temperature: float,
    ) -> dict[str, Any]:
        provider = getattr(vlm, "provider", None)
        configured_max_tokens = getattr(vlm, "max_tokens", None)
        effective_max_tokens = (
            configured_max_tokens if configured_max_tokens is not None else max_tokens
        )
        # A wrapped credential owns its model.  The model passed by AgentLoop is
        # the outer/default model and must not overwrite a backup credential.
        effective_model = (
            getattr(vlm, "model", None)
            if vlm is not self._vlm
            else model or getattr(vlm, "model", None)
        ) or self._default_model

        if provider == "volcengine":
            kwargs: dict[str, Any] = {
                "model": effective_model,
                "messages": messages,
                "temperature": getattr(vlm, "temperature", temperature),
                "thinking": {"type": "enabled" if getattr(vlm, "thinking", False) else "disabled"},
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            if effective_max_tokens is not None:
                kwargs["max_tokens"] = effective_max_tokens
            extra_headers = getattr(vlm, "extra_headers", None)
            if extra_headers:
                kwargs["extra_headers"] = extra_headers
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
            return kwargs

        kwargs = vlm._build_text_kwargs(
            messages=messages,
            tools=tools,
            tool_choice="auto" if tools else None,
            thinking=getattr(vlm, "thinking", None),
        )
        kwargs["model"] = effective_model
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}
        if effective_max_tokens is not None and not (
            "max_tokens" in kwargs or "max_completion_tokens" in kwargs
        ):
            token_key = "max_completion_tokens" if "reasoning_effort" in kwargs else "max_tokens"
            kwargs[token_key] = effective_max_tokens
        return kwargs

    @staticmethod
    def _usage_value(usage: Any, name: str) -> int:
        if isinstance(usage, dict):
            return int(usage.get(name, 0) or 0)
        return int(getattr(usage, name, 0) or 0)

    @classmethod
    def _parse_usage(cls, raw_usage: Any) -> dict[str, int]:
        if not raw_usage:
            return {}

        usage = {
            "prompt_tokens": cls._usage_value(raw_usage, "prompt_tokens"),
            "completion_tokens": cls._usage_value(raw_usage, "completion_tokens"),
            "total_tokens": cls._usage_value(raw_usage, "total_tokens"),
        }
        prompt_details = (
            raw_usage.get("prompt_tokens_details")
            if isinstance(raw_usage, dict)
            else getattr(raw_usage, "prompt_tokens_details", None)
        )
        completion_details = (
            raw_usage.get("completion_tokens_details")
            if isinstance(raw_usage, dict)
            else getattr(raw_usage, "completion_tokens_details", None)
        )
        cached = cls._usage_value(prompt_details, "cached_tokens") if prompt_details else 0
        reasoning = (
            cls._usage_value(completion_details, "reasoning_tokens") if completion_details else 0
        )
        if cached:
            usage["cache_read_input_tokens"] = cached
        if reasoning:
            usage["reasoning_tokens"] = reasoning
        return usage

    def _record_vlm_usage(
        self,
        vlm: Any,
        usage: dict[str, int],
        duration_seconds: float,
    ) -> None:
        update_token_usage = getattr(vlm, "update_token_usage", None)
        if not callable(update_token_usage):
            return
        try:
            update_token_usage(
                model_name=getattr(vlm, "model", None) or self._default_model,
                provider=getattr(vlm, "provider", None) or "unknown",
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                duration_seconds=duration_seconds,
                prompt_cached_tokens=usage.get("cache_read_input_tokens", 0),
                completion_reasoning_tokens=usage.get("reasoning_tokens", 0),
            )
        except Exception as e:
            logger.debug(f"[VLM] Failed to record stream token usage: {e}")

    def _convert_response(self, result) -> LLMResponse:
        """Convert VLMResponse (or str) to LLMResponse."""
        if isinstance(result, str):
            return LLMResponse(content=result, finish_reason="stop")

        tool_calls = []
        for tc in result.tool_calls:
            tool_calls.append(
                ToolCallRequest(
                    id=tc.id,
                    name=tc.name,
                    arguments=tc.arguments,
                    tokens=0,
                )
            )

        return LLMResponse(
            content=result.content,
            tool_calls=tool_calls,
            finish_reason=result.finish_reason,
            usage=result.usage,
            reasoning_content=result.reasoning_content,
        )

    def get_default_model(self) -> str:
        return self._default_model

    # ------------------------------------------------------------------
    # Langfuse helpers (same pattern as LiteLLMProvider.chat())
    # ------------------------------------------------------------------

    def _end_langfuse_observation(self, obs, llm_response: LLMResponse) -> None:
        try:
            output_text = llm_response.content or ""
            if llm_response.tool_calls:
                output_text = (
                    output_text or f"[Tool calls: {[tc.name for tc in llm_response.tool_calls]}]"
                )

            update_kwargs: dict[str, Any] = {
                "output": output_text,
                "metadata": {
                    "finish_reason": llm_response.finish_reason,
                    **(
                        {"response_id": get_current_response_id()}
                        if get_current_response_id()
                        else {}
                    ),
                },
            }

            if llm_response.usage:
                usage_details: dict[str, Any] = {
                    "input": llm_response.usage.get("prompt_tokens", 0),
                    "output": llm_response.usage.get("completion_tokens", 0),
                }
                cache_read_tokens = llm_response.usage.get(
                    "cache_read_input_tokens"
                ) or llm_response.usage.get("prompt_tokens_details", {}).get("cached_tokens")
                if cache_read_tokens:
                    usage_details["cache_read_input_tokens"] = cache_read_tokens
                update_kwargs["usage_details"] = usage_details

            response_id = get_current_response_id()
            if response_id:
                update_kwargs["metadata"] = self._langfuse.update_generation_metadata(
                    response_id,
                    update_kwargs.get("metadata", {}),
                )

            if hasattr(obs, "update"):
                try:
                    obs.update(**update_kwargs)
                except Exception as e:
                    logger.debug(f"[LANGFUSE] Failed to update observation: {e}")

            if hasattr(obs, "end"):
                try:
                    obs.end()
                except Exception as e:
                    logger.debug(f"[LANGFUSE] Failed to end observation: {e}")

            try:
                self._langfuse.flush()
            except Exception as e:
                logger.debug(f"[LANGFUSE] Failed to flush: {e}")
        except Exception:
            pass

    def _end_langfuse_observation_error(self, obs, error: Exception) -> None:
        try:
            if hasattr(obs, "update"):
                obs.update(
                    output=f"Error: {str(error)}",
                    metadata={
                        "error": str(error),
                        **(
                            {"response_id": get_current_response_id()}
                            if get_current_response_id()
                            else {}
                        ),
                    },
                )
            if hasattr(obs, "end"):
                obs.end()
            try:
                self._langfuse.flush()
            except Exception:
                pass
        except Exception:
            pass
