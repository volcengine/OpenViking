# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OpenAI VLM backend implementation"""

import base64
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse

from openviking.telemetry import tracer
from openviking.utils.async_client_cache import LoopScopedAsyncClientCache
from openviking.utils.multimodal import redact_image_data_urls
from openviking_cli.utils import get_logger

try:
    import openai
except ImportError:
    openai = None

from openviking.utils.image_search import detect_image_mime
from openviking.utils.model_retry import retry_async, retry_sync

from ..base import ToolCall, VLMBase, VLMResponse
from ..registry import DEFAULT_AZURE_API_VERSION

logger = get_logger(__name__)

#: Image MIME types every OpenAI-compatible vision endpoint decodes natively.
SUPPORTED_IMAGE_MIME_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
    }
)

#: Substrings in a provider error indicating the image payload itself was
#: rejected (undecodable or unsupported), as opposed to a transient failure.
_IMAGE_REJECTION_MARKERS = (
    "invalid image",
    "failed to load image",
    "image input",
    "unable to process image",
)

#: Runtime blacklist of image MIME types the endpoint rejected with an
#: "invalid image" error. Keyed by (provider, api_base, model) so distinct
#: endpoints keep independent capability views. Lives for the process
#: lifetime: once a format is rejected, stop resending it.
_blacklisted_image_mimes: Dict[Tuple[str, str, str], set] = {}


def _endpoint_key(
    provider: Optional[str], api_base: Optional[str], model: Optional[str]
) -> Tuple[str, str, str]:
    return (provider or "", (api_base or "").rstrip("/"), model or "")


def is_image_mime_blacklisted(
    provider: Optional[str], api_base: Optional[str], model: Optional[str], mime: Optional[str]
) -> bool:
    """Return True when the endpoint rejected this image MIME type at runtime."""
    if not mime:
        return False
    return mime in _blacklisted_image_mimes.get(_endpoint_key(provider, api_base, model), set())


def blacklist_image_mime(
    provider: Optional[str], api_base: Optional[str], model: Optional[str], mime: str
) -> None:
    """Record that the endpoint rejected an image of this MIME type."""
    if not mime:
        return
    _blacklisted_image_mimes.setdefault(_endpoint_key(provider, api_base, model), set()).add(mime)


def is_image_error(e: BaseException) -> bool:
    """Return True when a provider error rejects the image payload itself."""
    text = str(e).lower()
    return any(marker in text for marker in _IMAGE_REJECTION_MARKERS)


_DASHSCOPE_HOSTS = {
    "dashscope.aliyuncs.com",
    "dashscope-intl.aliyuncs.com",
}


_REASONING_MODEL_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def _is_reasoning_model(model: Optional[str]) -> bool:
    """OpenAI reasoning-model families reject `max_tokens` and non-default `temperature`.

    They require `max_completion_tokens` and only accept `temperature=1` (server default).
    """
    if not model:
        return False
    name = model.lower()
    return any(name.startswith(p) for p in _REASONING_MODEL_PREFIXES)


def _build_openai_client_kwargs(
    provider: str,
    api_key: str,
    api_base: str,
    api_version: str | None,
    extra_headers: Dict[str, str] | None,
    timeout: float = 600.0,
) -> Dict[str, Any]:
    """Build kwargs dict shared by sync and async OpenAI/Azure client constructors."""
    if provider == "azure":
        if not api_base:
            raise ValueError("api_base (Azure endpoint) is required for Azure provider")
        kwargs: Dict[str, Any] = {
            "api_key": api_key,
            "azure_endpoint": api_base,
            "api_version": api_version or DEFAULT_AZURE_API_VERSION,
            "timeout": timeout,
        }
    else:
        kwargs = {"api_key": api_key, "base_url": api_base}
    kwargs["timeout"] = timeout
    # OpenViking owns provider retry/backoff via retry_sync/retry_async.
    kwargs["max_retries"] = 0
    if extra_headers:
        kwargs["default_headers"] = extra_headers
    return kwargs


class OpenAIVLM(VLMBase):
    """OpenAI / Azure OpenAI VLM backend"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._sync_client = None
        self._async_client_cache = LoopScopedAsyncClientCache()
        self.api_version = config.get("api_version")
        self.reasoning_effort = config.get("reasoning_effort", "low")

    def get_client(self):
        """Get sync client"""
        if self._sync_client is None:
            if openai is None:
                raise ImportError("Please install openai: pip install openai")
            kwargs = _build_openai_client_kwargs(
                self.provider,
                self.api_key,
                self.api_base,
                self.api_version,
                self.extra_headers,
                self.timeout,
            )
            if self.provider == "azure":
                self._sync_client = openai.AzureOpenAI(**kwargs)
            else:
                self._sync_client = openai.OpenAI(**kwargs)
        return self._sync_client

    def _build_async_client(self):
        """Create an async client for the current backend."""
        if openai is None:
            raise ImportError("Please install openai: pip install openai")
        kwargs = _build_openai_client_kwargs(
            self.provider,
            self.api_key,
            self.api_base,
            self.api_version,
            self.extra_headers,
            self.timeout,
        )
        if self.provider == "azure":
            return openai.AsyncAzureOpenAI(**kwargs)
        return openai.AsyncOpenAI(**kwargs)

    def get_async_client(self):
        """Get an async client scoped to the current event loop."""
        return self._async_client_cache.get(self._build_async_client)

    def _supports_enable_thinking(self) -> bool:
        """Return True for OpenAI-compatible DashScope endpoints that accept enable_thinking."""
        if self.provider != "openai":
            return False

        if isinstance(self.model, str) and self.model.lower().startswith("dashscope/"):
            return True

        if not self.api_base:
            return False

        try:
            host = urlparse(self.api_base).hostname or ""
        except ValueError:
            return False

        return host.lower() in _DASHSCOPE_HOSTS

    def _apply_provider_specific_extra_body(self, kwargs: Dict[str, Any], thinking: bool) -> None:
        """Attach provider-specific raw body parameters understood by compatible APIs."""
        extra_body = dict(self.extra_request_body)
        if self._supports_enable_thinking():
            extra_body["enable_thinking"] = bool(thinking)
        if extra_body:
            kwargs["extra_body"] = extra_body

    def _update_token_usage_from_response(
        self,
        response,
        duration_seconds: float = 0.0,
    ):
        if hasattr(response, "usage") and response.usage:
            tracer.info(f"response.usage={response.usage}")
            prompt_tokens = response.usage.prompt_tokens
            completion_tokens = response.usage.completion_tokens
            prompt_tokens_details = getattr(response.usage, "prompt_tokens_details", None)
            completion_tokens_details = getattr(response.usage, "completion_tokens_details", None)
            prompt_cached_tokens = getattr(prompt_tokens_details, "cached_tokens", 0) or 0
            completion_reasoning_tokens = (
                getattr(completion_tokens_details, "reasoning_tokens", 0) or 0
            )
            self.update_token_usage(
                model_name=self.model or "gpt-4o-mini",
                provider=self.provider,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                duration_seconds=duration_seconds,
                prompt_cached_tokens=prompt_cached_tokens,
                completion_reasoning_tokens=completion_reasoning_tokens,
            )
        return

    def _parse_tool_calls(self, message) -> List[ToolCall]:
        """Parse tool calls from OpenAI response message."""
        tool_calls = []
        if hasattr(message, "tool_calls") and message.tool_calls:
            for tc in message.tool_calls:
                args = tc.function.arguments
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"raw": args}
                tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
        return tool_calls

    def _build_vlm_response(self, response, has_tools: bool) -> Union[str, VLMResponse]:
        """Build response from OpenAI response. Returns str or VLMResponse based on has_tools."""
        if isinstance(response, str):
            return VLMResponse(content=response) if has_tools else response

        choice = response.choices[0]
        message = choice.message
        tracer.info(f"result={message.content}")
        if has_tools:
            usage = {}
            if hasattr(response, "usage") and response.usage:
                usage = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                    "prompt_tokens_details": getattr(response.usage, "prompt_tokens_details", None),
                }

            return VLMResponse(
                content=message.content,
                tool_calls=self._parse_tool_calls(message),
                finish_reason=choice.finish_reason or "stop",
                usage=usage,
            )
        return message.content or ""

    def _build_text_kwargs(
        self,
        prompt: str = "",
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        thinking: Optional[bool] = None,
    ) -> Dict[str, Any]:
        effective_thinking = self.thinking if thinking is None else thinking
        kwargs_messages = messages or [{"role": "user", "content": prompt}]
        model = self.model or "gpt-4o-mini"
        is_reasoning = _is_reasoning_model(model)
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": kwargs_messages,
        }
        if is_reasoning:
            kwargs["reasoning_effort"] = self.reasoning_effort
        else:
            kwargs["temperature"] = self.temperature
        self._apply_provider_specific_extra_body(kwargs, effective_thinking)
        if self.max_tokens is not None:
            kwargs["max_completion_tokens" if is_reasoning else "max_tokens"] = self.max_tokens
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"
        return kwargs

    def _build_vision_kwargs(
        self,
        prompt: str = "",
        images: Optional[List[Union[str, Path, bytes]]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        thinking: Optional[bool] = None,
    ) -> Dict[str, Any]:
        effective_thinking = self.thinking if thinking is None else thinking
        if messages:
            kwargs_messages = messages
        else:
            content = []
            if images:
                prepared = [
                    part
                    for part in (self._prepare_image(img) for img in images)
                    if part is not None
                ]
                if not prepared and not prompt:
                    raise ValueError(
                        "All images were skipped (unrecognized or endpoint-rejected format) "
                        "and no prompt was provided"
                    )
                content.extend(prepared)
            if prompt:
                content.append({"type": "text", "text": prompt})
            kwargs_messages = [{"role": "user", "content": content}]

        model = self.model or "gpt-4o-mini"
        is_reasoning = _is_reasoning_model(model)
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": kwargs_messages,
        }
        if is_reasoning:
            kwargs["reasoning_effort"] = self.reasoning_effort
        else:
            kwargs["temperature"] = self.temperature
        self._apply_provider_specific_extra_body(kwargs, effective_thinking)
        if self.max_tokens is not None:
            kwargs["max_completion_tokens" if is_reasoning else "max_tokens"] = self.max_tokens
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"
        return kwargs

    def _extract_completion_content(self, response, elapsed: float) -> str:
        self._update_token_usage_from_response(response, duration_seconds=elapsed)
        content = self._extract_content_from_response(response)
        return self._clean_response(content)

    async def _extract_completion_content_async(self, response, elapsed: float) -> str:
        self._update_token_usage_from_response(response, duration_seconds=elapsed)
        content = self._extract_content_from_response(response)
        return self._clean_response(content)

    def get_completion(
        self,
        prompt: str = "",
        thinking: Optional[bool] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        """Get text completion"""
        effective_thinking = self.thinking if thinking is None else thinking
        client = self.get_client()
        kwargs = self._build_text_kwargs(prompt, tools, tool_choice, messages, effective_thinking)

        def _call() -> Union[str, VLMResponse]:
            t0 = time.perf_counter()
            response = client.chat.completions.create(**kwargs)
            elapsed = time.perf_counter() - t0
            if tools is not None:
                self._update_token_usage_from_response(response, duration_seconds=elapsed)
                return self._build_vlm_response(response, has_tools=True)
            return self._extract_completion_content(response, elapsed)

        return retry_sync(
            _call,
            max_retries=self.max_retries,
            logger=logger,
            operation_name="OpenAI VLM completion",
        )

    @tracer("openai.vlm.call", ignore_result=True, ignore_args=["messages"])
    async def get_completion_async(
        self,
        prompt: str = "",
        thinking: Optional[bool] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        """Get text completion asynchronously"""
        effective_thinking = self.thinking if thinking is None else thinking
        client = self.get_async_client()
        kwargs = self._build_text_kwargs(prompt, tools, tool_choice, messages, effective_thinking)

        async def _call() -> Union[str, VLMResponse]:
            t0 = time.perf_counter()
            response = await client.chat.completions.create(**kwargs)
            elapsed = time.perf_counter() - t0
            if tools is not None:
                self._update_token_usage_from_response(response, duration_seconds=elapsed)
                return self._build_vlm_response(response, has_tools=True)
            return await self._extract_completion_content_async(response, elapsed)

        # 用 tracer.info 打印请求
        tracer.info(
            f"messages={json.dumps(redact_image_data_urls(kwargs), ensure_ascii=False, indent=2)}"
        )

        return await retry_async(
            _call,
            max_retries=self.max_retries,
            logger=logger,
            operation_name="OpenAI VLM async completion",
        )

    def _detect_image_format(self, data: bytes) -> Optional[str]:
        """Detect the image MIME type from content via libmagic.

        Returns the ``image/*`` MIME type, or ``None`` when the bytes are not
        a recognized image. Previously unknown bytes were mislabeled
        ``image/png`` and sent to the endpoint, which rejected them.
        """
        if not data:
            return None
        mime = detect_image_mime(data)
        if mime is None:
            logger.warning(
                f"[OpenAIVLM] Non-image data sent as image, magic bytes: {data[:8].hex()}"
            )
        return mime

    def _prepare_image(self, image: Union[str, Path, bytes]) -> Optional[Dict[str, Any]]:
        """Prepare image data for vision completion.

        Returns ``None`` when the image must be skipped: unrecognized
        content, or a MIME type the endpoint already rejected at runtime.
        """
        if isinstance(image, bytes):
            mime_type = self._detect_image_format(image)
            if mime_type is None:
                return None
            if self._is_mime_blacklisted(mime_type):
                logger.info(f"[OpenAIVLM] Skipping {mime_type} image: format rejected by endpoint")
                return None
            b64 = base64.b64encode(image).decode("utf-8")
            return {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{b64}"},
            }
        if isinstance(image, Path) or (
            isinstance(image, str) and not image.startswith(("http://", "https://"))
        ):
            path = Path(image)
            with open(path, "rb") as f:
                data = f.read()
            mime_type = self._detect_image_format(data)
            if mime_type is None:
                logger.warning(f"[OpenAIVLM] Skipping non-image file: {path}")
                return None
            if self._is_mime_blacklisted(mime_type):
                logger.info(f"[OpenAIVLM] Skipping {mime_type} image: format rejected by endpoint")
                return None
            b64 = base64.b64encode(data).decode("utf-8")
            return {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{b64}"},
            }
        return {"type": "image_url", "image_url": {"url": image}}

    def _is_mime_blacklisted(self, mime: str) -> bool:
        return is_image_mime_blacklisted(self.provider, self.api_base, self.model, mime)

    def _blacklist_mime(self, mime: str) -> None:
        blacklist_image_mime(self.provider, self.api_base, self.model, mime)
        logger.warning(
            f"[OpenAIVLM] Endpoint rejected {mime} images; blacklisting format "
            f"for model {self.model} until process restart"
        )

    def get_vision_completion(
        self,
        prompt: str = "",
        images: Optional[List[Union[str, Path, bytes]]] = None,
        thinking: Optional[bool] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        """Get vision completion"""
        effective_thinking = self.thinking if thinking is None else thinking
        client = self.get_client()
        kwargs = self._build_vision_kwargs(
            prompt, images, tools, tool_choice, messages, effective_thinking
        )

        def _call() -> Union[str, VLMResponse]:
            t0 = time.perf_counter()
            response = client.chat.completions.create(**kwargs)
            elapsed = time.perf_counter() - t0
            if tools is not None:
                self._update_token_usage_from_response(response, duration_seconds=elapsed)
                return self._build_vlm_response(response, has_tools=True)
            return self._extract_completion_content(response, elapsed)

        try:
            return retry_sync(
                _call,
                max_retries=self.max_retries,
                logger=logger,
                operation_name="OpenAI VLM vision completion",
            )
        except Exception as e:
            if is_image_error(e):
                self._blacklist_rejected_image_mimes(images)
            raise

    def _blacklist_rejected_image_mimes(
        self, images: Optional[List[Union[str, Path, bytes]]]
    ) -> None:
        """Learn from an endpoint 'invalid image' rejection.

        Only MIME types outside the universally-decodable set are
        blacklisted: a rejected PNG/JPEG/GIF/WebP is far more likely a
        transient or payload problem than a format problem.
        """
        for image in images or []:
            if isinstance(image, (bytes, bytearray, memoryview)):
                mime = self._detect_image_format(bytes(image))
            elif isinstance(image, Path) or (
                isinstance(image, str) and not image.startswith(("http://", "https://"))
            ):
                try:
                    with open(image, "rb") as f:
                        mime = self._detect_image_format(f.read())
                except OSError:
                    mime = None
            else:
                mime = None
            if mime is not None and mime not in SUPPORTED_IMAGE_MIME_TYPES:
                self._blacklist_mime(mime)

    async def get_vision_completion_async(
        self,
        prompt: str = "",
        images: Optional[List[Union[str, Path, bytes]]] = None,
        thinking: Optional[bool] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        """Get vision completion asynchronously"""
        effective_thinking = self.thinking if thinking is None else thinking
        client = self.get_async_client()
        kwargs = self._build_vision_kwargs(
            prompt, images, tools, tool_choice, messages, effective_thinking
        )

        async def _call() -> Union[str, VLMResponse]:
            t0 = time.perf_counter()
            response = await client.chat.completions.create(**kwargs)
            elapsed = time.perf_counter() - t0
            if tools is not None:
                self._update_token_usage_from_response(response, duration_seconds=elapsed)
                return self._build_vlm_response(response, has_tools=True)
            return await self._extract_completion_content_async(response, elapsed)

        try:
            return await retry_async(
                _call,
                max_retries=self.max_retries,
                logger=logger,
                operation_name="OpenAI VLM async vision completion",
            )
        except Exception as e:
            if is_image_error(e):
                self._blacklist_rejected_image_mimes(images)
            raise
