"""LiteLLM provider implementation for multi-provider support."""

import json
import os
from typing import Any

from loguru import logger
import litellm
from litellm import acompletion

from vikingbot.integrations.langfuse import LangfuseClient
from vikingbot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from vikingbot.providers.registry import find_by_model, find_gateway
from vikingbot.utils.helpers import cal_str_tokens


class LiteLLMProvider(LLMProvider):
    """
    LLM provider using LiteLLM for multi-provider support.

    Supports OpenRouter, Anthropic, OpenAI, Gemini, MiniMax, and many other providers through
    a unified interface.  Provider-specific logic is driven by the registry
    (see providers/registry.py) — no if-elif chains needed here.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        default_model: str = "anthropic/claude-opus-4-5",
        extra_headers: dict[str, str] | None = None,
        provider_name: str | None = None,
        langfuse_client: LangfuseClient | None = None,
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.extra_headers = extra_headers or {}
        self.langfuse = langfuse_client or LangfuseClient.get_instance()

        # Detect gateway / local deployment.
        # provider_name (from config key) is the primary signal;
        # api_key / api_base are fallback for auto-detection.
        self._gateway = find_gateway(provider_name, api_key, api_base)

        # Configure environment variables
        if api_key:
            self._setup_env(api_key, api_base, default_model)

        if api_base:
            litellm.api_base = api_base

        # Disable LiteLLM logging noise
        litellm.suppress_debug_info = True
        # Drop unsupported parameters for providers (e.g., gpt-5 rejects some params)
        litellm.drop_params = True

    def _setup_env(self, api_key: str, api_base: str | None, model: str) -> None:
        """Set environment variables based on detected provider."""
        spec = self._gateway or find_by_model(model)
        if not spec:
            return

        # Gateway/local overrides existing env; standard provider doesn't
        if self._gateway:
            os.environ[spec.env_key] = api_key
        else:
            os.environ.setdefault(spec.env_key, api_key)

        # Resolve env_extras placeholders:
        #   {api_key}  → user's API key
        #   {api_base} → user's api_base, falling back to spec.default_api_base
        effective_base = api_base or spec.default_api_base
        for env_name, env_val in spec.env_extras:
            resolved = env_val.replace("{api_key}", api_key)
            resolved = resolved.replace("{api_base}", effective_base)
            os.environ.setdefault(env_name, resolved)

    def _resolve_model(self, model: str) -> str:
        """Resolve model name by applying provider/gateway prefixes."""
        if self._gateway:
            # Gateway mode: apply gateway prefix, skip provider-specific prefixes
            prefix = self._gateway.litellm_prefix
            if self._gateway.strip_model_prefix:
                model = model.split("/")[-1]
            if prefix and not model.startswith(f"{prefix}/"):
                model = f"{prefix}/{model}"
            return model

        # Standard mode: auto-prefix for known providers
        spec = find_by_model(model)
        if spec and spec.litellm_prefix:
            if not any(model.startswith(s) for s in spec.skip_prefixes):
                model = f"{spec.litellm_prefix}/{model}"

        return model

    def _apply_model_overrides(self, model: str, kwargs: dict[str, Any]) -> None:
        """Apply model-specific parameter overrides from the registry."""
        model_lower = model.lower()
        spec = find_by_model(model)
        if spec:
            for pattern, overrides in spec.model_overrides:
                if pattern in model_lower:
                    kwargs.update(overrides)
                    return

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
        Send a chat completion request via LiteLLM.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            tools: Optional list of tool definitions in OpenAI format.
            model: Model identifier (e.g., 'anthropic/claude-sonnet-4-5').
            max_tokens: Maximum tokens in response.
            temperature: Sampling temperature.
            session_id: Optional session ID for tracing.

        Returns:
            LLMResponse with content and/or tool calls.
        """
        model = self._resolve_model(model or self.default_model)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        # Apply model-specific overrides (e.g. kimi-k2.5 temperature)
        self._apply_model_overrides(model, kwargs)

        # Pass api_key directly — more reliable than env vars alone
        if self.api_key:
            kwargs["api_key"] = self.api_key

        # Pass api_base for custom endpoints
        if self.api_base:
            kwargs["api_base"] = self.api_base

        # Pass extra headers (e.g. APP-Code for AiHubMix)
        if self.extra_headers:
            kwargs["extra_headers"] = self.extra_headers

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        # Direct Langfuse v3 SDK usage
        # Note: session_id is set via propagate_attributes in loop.py, not here
        langfuse_generation = None
        try:
            if self.langfuse.enabled and self.langfuse._client:
                metadata = {"has_tools": tools is not None}
                langfuse_generation = self.langfuse._client.start_generation(
                    name="llm-chat",
                    model=model,
                    input=messages,
                    metadata=metadata,
                )

            response = await acompletion(**kwargs)
            llm_response = self._parse_response(response)

            # Update and end Langfuse generation
            if langfuse_generation:
                output_text = llm_response.content or ""
                if llm_response.tool_calls:
                    output_text = (
                        output_text
                        or f"[Tool calls: {[tc.name for tc in llm_response.tool_calls]}]"
                    )

                # Update generation with output and usage
                update_kwargs: dict[str, Any] = {
                    "output": output_text,
                    "metadata": {"finish_reason": llm_response.finish_reason},
                }

                if llm_response.usage:
                    # Langfuse v3 SDK expects "usage_details" with "input" and "output" keys
                    usage_details: dict[str, Any] = {
                        "input": llm_response.usage.get("prompt_tokens", 0),
                        "output": llm_response.usage.get("completion_tokens", 0),
                    }

                    # Add cache read tokens if available (OpenAI/Anthropic prompt caching)
                    # Try multiple possible field names for cached tokens
                    cache_read_tokens = llm_response.usage.get(
                        "cache_read_input_tokens"
                    ) or llm_response.usage.get("prompt_tokens_details", {}).get("cached_tokens")
                    if cache_read_tokens:
                        usage_details["cache_read_input_tokens"] = cache_read_tokens

                    update_kwargs["usage_details"] = usage_details
                    # Log the usage details being sent to Langfuse
                    # logger.info(f"[LANGFUSE] Updating generation with usage_details: {usage_details}")

                langfuse_generation.update(**update_kwargs)
                langfuse_generation.end()
                self.langfuse.flush()

            return llm_response
        except Exception as e:
            # End Langfuse generation with error
            if langfuse_generation:
                langfuse_generation.update(
                    output=f"Error: {str(e)}",
                    metadata={"error": str(e)},
                )
                langfuse_generation.end()
                self.langfuse.flush()
            # Return error as content for graceful handling
            return LLMResponse(
                content=f"Error calling LLM: {str(e)}",
                finish_reason="error",
            )

    def _parse_response(self, response: Any) -> LLMResponse:
        """Parse LiteLLM response into our standard format."""
        choice = response.choices[0]
        message = choice.message

        tool_calls = []
        if hasattr(message, "tool_calls") and message.tool_calls:
            for tc in message.tool_calls:
                # Parse arguments from JSON string if needed
                args = tc.function.arguments
                tokens = cal_str_tokens(tc.function.name, text_type="en")
                if isinstance(args, str):
                    try:
                        tokens += cal_str_tokens(args, text_type="mixed")
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"raw": args}

                tool_calls.append(
                    ToolCallRequest(id=tc.id, name=tc.function.name, arguments=args, tokens=tokens)
                )

        usage = {}
        if hasattr(response, "usage") and response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

            # Extract cached tokens from various provider formats
            # OpenAI style: prompt_tokens_details.cached_tokens
            if hasattr(response.usage, "prompt_tokens_details"):
                details = response.usage.prompt_tokens_details
                if details and hasattr(details, "cached_tokens"):
                    cached = details.cached_tokens
                    if cached:
                        usage["cache_read_input_tokens"] = cached
            # Anthropic style: cache_read_input_tokens
            elif hasattr(response.usage, "cache_read_input_tokens"):
                cached = response.usage.cache_read_input_tokens
                if cached:
                    usage["cache_read_input_tokens"] = cached

        reasoning_content = getattr(message, "reasoning_content", None)

        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage=usage,
            reasoning_content=reasoning_content,
        )

    def get_default_model(self) -> str:
        """Get the default model."""
        return self.default_model
