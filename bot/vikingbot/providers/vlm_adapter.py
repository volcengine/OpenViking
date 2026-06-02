"""VLM-to-LLMProvider adapter.

Wraps openviking VLM backends (VLMBase) to implement the vikingbot
LLMProvider interface, so that bot.agents.provider / bot.agents.model
configuration semantics are consistent with openviking server's vlm section.
"""

from typing import Any

from loguru import logger

from vikingbot.integrations.langfuse import LangfuseClient
from vikingbot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
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
        max_tokens: int = 4096,
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
                        input=messages,
                        metadata=metadata,
                    )
                    if response_id:
                        self._langfuse.register_generation(
                            response_id, langfuse_observation, metadata=metadata
                        )

            # --- Call VLM backend ---
            result = await self._vlm.get_completion_async(
                messages=messages,
                tools=tools,
                tool_choice="auto" if tools else None,
            )

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
                    output_text
                    or f"[Tool calls: {[tc.name for tc in llm_response.tool_calls]}]"
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
