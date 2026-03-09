"""Langfuse integration for LLM observability."""

from contextlib import contextmanager
from typing import Any, Generator

from loguru import logger

# Try to import langfuse - will be None if not installed
Langfuse = None
propagate_attributes = None

try:
    from langfuse import Langfuse
    from langfuse import propagate_attributes as _propagate_attributes

    propagate_attributes = _propagate_attributes
except ImportError:
    pass


class LangfuseClient:
    """Wrapper for Langfuse client with optional support."""

    _instance: "LangfuseClient | None" = None

    def __init__(
        self,
        enabled: bool = False,
        secret_key: str = "",
        public_key: str = "",
        base_url: str = "https://cloud.langfuse.com",
    ):
        self._client = None
        self.enabled = enabled

        if not self.enabled:
            return

        if Langfuse is None:
            logger.warning(
                'Langfuse not installed. Install with: uv pip install openviking[bot-langfuse] (or uv pip install -e ".[bot-langfuse]" for local dev). Configure in ~/.openviking/ov.conf under bot.langfuse'
            )
            self.enabled = False
            return

        if not secret_key:
            logger.warning(
                "Langfuse enabled but no secret_key provided. Configure in ~/.openviking/ov.conf under bot.langfuse"
            )
            self.enabled = False
            return

        try:
            self._client = Langfuse(
                secret_key=secret_key,
                public_key=public_key,
                host=base_url,
            )
            self._client.auth_check()
        except Exception as e:
            logger.warning(f"Langfuse initialized failed: {type(e).__name__}: {e}")
            self.enabled = False
            self._client = None

    @classmethod
    def get_instance(cls) -> "LangfuseClient":
        """Get the singleton instance."""
        if cls._instance is None:
            logger.warning("[LANGFUSE] disabled")
            cls._instance = LangfuseClient(enabled=False)
        return cls._instance

    @classmethod
    def set_instance(cls, instance: "LangfuseClient") -> None:
        """Set the singleton instance."""
        cls._instance = instance

    def flush(self) -> None:
        """Flush pending events to Langfuse."""
        if self.enabled and self._client:
            self._client.flush()

    @contextmanager
    def propagate_attributes(
        self,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> Generator[None, None, None]:
        """
        Propagate attributes (session_id, user_id) to all nested observations.

        Args:
            session_id: Optional session ID to associate with all nested observations
            user_id: Optional user ID to associate with all nested observations
        """
        if not self.enabled:
            logger.warning("[LANGFUSE] propagate_attributes skipped: Langfuse client not enabled")
            yield
            return
        if not self._client:
            logger.warning(
                "[LANGFUSE] propagate_attributes skipped: Langfuse client not initialized"
            )
            yield
            return

        try:
            propagate_kwargs = {}
            if session_id:
                propagate_kwargs["session_id"] = session_id
            if user_id:
                propagate_kwargs["user_id"] = user_id

            if not propagate_kwargs:
                yield
                return

            # Use module-level propagate_attributes from langfuse SDK v3
            global propagate_attributes
            if propagate_attributes is not None:
                logger.info(f"[LANGFUSE] Propagating attributes: {list(propagate_kwargs.keys())}")
                with propagate_attributes(**propagate_kwargs):
                    yield
            else:
                logger.warning(
                    "[LANGFUSE] propagate_attributes not available (SDK version may not support it)"
                )
                yield
        except Exception as e:
            logger.debug(f"[LANGFUSE] propagate_attributes error: {e}")
            yield

    @contextmanager
    def trace(
        self,
        name: str,
        session_id: str | None = None,
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Generator[Any, None, None]:
        """
        Create a trace context manager.
        In v3 SDK, trace is implicitly created by first span/generation.
        """
        if not self.enabled or not self._client:
            yield None
            return

        try:
            # In v3, we use start_as_current_span to create the root span
            with self._client.start_as_current_span(
                name=name,
                session_id=session_id,
                user_id=user_id,
                metadata=metadata or {},
            ) as span:
                yield span
        except Exception as e:
            logger.debug(f"Langfuse trace error: {e}")
            yield None

    @contextmanager
    def span(
        self,
        name: str,
        trace_id: str | None = None,
        parent_observation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Generator[Any, None, None]:
        """Create a span context manager."""
        if not self.enabled or not self._client:
            yield None
            return

        try:
            with self._client.start_as_current_span(
                name=name,
                metadata=metadata or {},
            ) as span:
                yield span
        except Exception as e:
            logger.debug(f"Langfuse span error: {e}")
            yield None

    @contextmanager
    def generation(
        self,
        name: str,
        model: str,
        trace_id: str | None = None,
        parent_observation_id: str | None = None,
        prompt: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Generator[Any, None, None]:
        """
        Create a generation context manager for LLM calls.

        Args:
            name: Name of the generation
            model: Model name
            trace_id: Optional trace ID (not used in v3)
            parent_observation_id: Optional parent observation ID (not used in v3)
            prompt: Optional prompt messages
            metadata: Optional metadata
        """
        if not self.enabled or not self._client:
            yield None
            return

        try:
            with self._client.start_as_current_generation(
                name=name,
                model=model,
                input=prompt,
                metadata=metadata or {},
            ) as generation:
                yield generation
        except Exception as e:
            logger.debug(f"Langfuse generation error: {e}")
            yield None

    def update_generation(
        self,
        generation: Any,
        output: str | None = None,
        usage: dict[str, int] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Update a generation with output and usage."""
        if not self.enabled or not generation:
            return

        try:
            update_kwargs: dict[str, Any] = {}
            if output is not None:
                update_kwargs["output"] = output
            if usage:
                update_kwargs["usage"] = {
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                }
            if metadata:
                if hasattr(generation, "metadata") and generation.metadata:
                    update_kwargs["metadata"] = {**generation.metadata, **metadata}
                else:
                    update_kwargs["metadata"] = metadata

            # In v3, update via the generation object's update method
            if hasattr(generation, "update"):
                generation.update(**update_kwargs)
            # Or use client's update_current_generation
            elif self._client and hasattr(self._client, "update_current_generation"):
                self._client.update_current_generation(**update_kwargs)

        except Exception as e:
            logger.debug(f"Langfuse update generation error: {e}")

    @contextmanager
    def tool_call(
        self,
        name: str,
        input: dict[str, Any] | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Generator[Any, None, None]:
        """
        Create a span for tool/function call execution.

        Args:
            name: Name of the tool/function
            input: Input arguments to the tool
            session_id: Optional session ID for tracing
            metadata: Optional metadata

        Yields:
            Langfuse span object or None if not enabled
        """
        if not self.enabled or not self._client:
            yield None
            return

        try:
            combined_metadata = metadata or {}
            if session_id:
                combined_metadata["session_id"] = session_id

            with self._client.start_as_current_span(
                name=f"tool:{name}",
                input=input,
                metadata=combined_metadata,
            ) as span:
                yield span
        except Exception as e:
            logger.debug(f"Langfuse tool call span error: {e}")
            yield None

    def end_tool_call(
        self,
        span: Any,
        output: str | None = None,
        success: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        End a tool call span with output and status.

        Args:
            span: The span object from tool_call()
            output: Output of the tool call
            success: Whether the tool call succeeded
            metadata: Optional additional metadata
        """
        if not self.enabled or not span:
            return

        try:
            update_kwargs: dict[str, Any] = {}
            if output is not None:
                update_kwargs["output"] = output

            combined_metadata = metadata or {}
            combined_metadata["success"] = success
            update_kwargs["metadata"] = combined_metadata

            if hasattr(span, "update"):
                span.update(**update_kwargs)

        except Exception as e:
            logger.debug(f"Langfuse end tool call error: {e}")
