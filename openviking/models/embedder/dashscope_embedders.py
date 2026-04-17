# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""DashScope Embedder Implementation

Supports both text (via OpenAI-compatible endpoint) and multimodal (via native
DashScope REST API) embedding modes.
"""

from typing import Any, Dict, List, Optional

import httpx
import openai

from openviking.models.embedder.base import (
    DenseEmbedderBase,
    EmbedResult,
    truncate_and_normalize,
)
from openviking.telemetry import get_current_telemetry
from openviking_cli.utils.logger import default_logger as logger

_DASHSCOPE_DIMENSIONS: Dict[str, int] = {
    "text-embedding-v3": 1024,
    "text-embedding-v4": 1024,
    "qwen3-vl-embedding": 2560,
    "qwen2.5-vl-embedding": 1024,
}

_DASHSCOPE_PREFIX_DIMENSIONS: Dict[str, int] = {
    "tongyi-embedding-vision-plus": 1152,
    "tongyi-embedding-vision-flash": 768,
}


def get_dashscope_model_default_dimension(model_name: str) -> int:
    """Return default embedding dimension for a DashScope model.

    Lookup order:
    1. Exact match in _DASHSCOPE_DIMENSIONS
    2. Prefix match in _DASHSCOPE_PREFIX_DIMENSIONS
    3. Fallback: 1024
    """
    if model_name in _DASHSCOPE_DIMENSIONS:
        return _DASHSCOPE_DIMENSIONS[model_name]
    for prefix, dim in _DASHSCOPE_PREFIX_DIMENSIONS.items():
        if model_name.startswith(prefix):
            return dim
    return 1024


class DashScopeDenseEmbedder(DenseEmbedderBase):
    """DashScope Dense Embedder — dual-mode (text / multimodal).

    Text mode uses the OpenAI-compatible endpoint; multimodal mode uses the
    native DashScope REST API.
    """

    def __init__(
        self,
        model_name: str,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        dimension: Optional[int] = None,
        input_type: str = "multimodal",
        enable_fusion: Optional[bool] = None,
        res_level: Optional[int] = None,
        max_video_frames: Optional[int] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(model_name, config)
        self.provider = "dashscope"

        self.api_key = api_key
        self.api_base = (api_base or "https://dashscope.aliyuncs.com").rstrip("/")
        self._input_type = input_type
        self.enable_fusion = enable_fusion
        self.res_level = res_level
        self.max_video_frames = max_video_frames

        if not self.api_key:
            raise ValueError("api_key is required")

        self._dimension = dimension or get_dashscope_model_default_dimension(model_name)

        # --- sync clients ---
        # Text mode: OpenAI-compatible
        self._openai_client = openai.OpenAI(
            api_key=self.api_key,
            base_url=f"{self.api_base}/compatible-mode/v1",
        )
        # Multimodal mode: httpx
        self._httpx_client = httpx.Client(
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=60.0,
        )
        self._multimodal_url = (
            f"{self.api_base}/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"
        )

        # --- async clients (lazy) ---
        self._async_openai_client: Optional[openai.AsyncOpenAI] = None
        self._async_httpx_client: Optional[httpx.AsyncClient] = None

    # ------------------------------------------------------------------
    # Token telemetry
    # ------------------------------------------------------------------

    def _update_telemetry_token_usage(self, response) -> None:
        """Track token usage from either an OpenAI SDK response or an httpx response."""
        usage = None

        # OpenAI SDK response object
        if hasattr(response, "usage"):
            usage = response.usage
        # httpx response — parse JSON body
        elif hasattr(response, "json"):
            try:
                body: Dict[str, Any] = response.json() if callable(response.json) else response.json
                usage = body.get("usage")
            except Exception:
                return

        if not usage:
            return

        def _val(key: str, default: int = 0) -> int:
            if isinstance(usage, dict):
                return int(usage.get(key, default) or default)
            return int(getattr(usage, key, default) or default)

        prompt_tokens = _val("prompt_tokens") or _val("input_tokens", 0)
        total_tokens = _val("total_tokens", prompt_tokens)
        completion_tokens = max(total_tokens - prompt_tokens, 0)

        get_current_telemetry().add_token_usage_by_source(
            "embedding", prompt_tokens, completion_tokens
        )
        self.update_token_usage(
            model_name=self.model_name,
            provider="dashscope",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    # ------------------------------------------------------------------
    # Multimodal helpers
    # ------------------------------------------------------------------

    def _multimodal_params(self) -> Dict[str, Any]:
        """Build parameters dict for multimodal requests, excluding None values."""
        return {
            k: v
            for k, v in {
                "dimension": self._dimension,
                "enable_fusion": self.enable_fusion,
                "res_level": self.res_level,
                "max_video_frames": self.max_video_frames,
            }.items()
            if v is not None
        }

    def _multimodal_body(self, text: str) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "model": self.model_name,
            "input": {"contents": [{"text": text}]},
        }
        params = self._multimodal_params()
        if params:
            body["parameters"] = params
        return body

    # ------------------------------------------------------------------
    # Lazy async clients
    # ------------------------------------------------------------------

    def _get_async_openai_client(self) -> openai.AsyncOpenAI:
        if self._async_openai_client is None:
            self._async_openai_client = openai.AsyncOpenAI(
                api_key=self.api_key,
                base_url=f"{self.api_base}/compatible-mode/v1",
            )
        return self._async_openai_client

    def _get_async_httpx_client(self) -> httpx.AsyncClient:
        if self._async_httpx_client is None:
            self._async_httpx_client = httpx.AsyncClient(
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=60.0,
            )
        return self._async_httpx_client

    # ------------------------------------------------------------------
    # embed
    # ------------------------------------------------------------------

    def embed(self, text: str, is_query: bool = False) -> EmbedResult:
        def _call() -> EmbedResult:
            if self._input_type == "text":
                response = self._openai_client.embeddings.create(
                    input=text, model=self.model_name, dimensions=self._dimension
                )
                self._update_telemetry_token_usage(response)
                vector = response.data[0].embedding
            else:
                resp = self._httpx_client.post(
                    self._multimodal_url, json=self._multimodal_body(text)
                )
                resp.raise_for_status()
                self._update_telemetry_token_usage(resp)
                vector = resp.json()["output"]["embeddings"][0]["embedding"]

            return EmbedResult(dense_vector=truncate_and_normalize(vector, self._dimension))

        try:
            return self._run_with_retry(_call, logger=logger, operation_name="DashScope embedding")
        except Exception as e:
            raise RuntimeError(f"DashScope embedding failed: {e}") from e

    # ------------------------------------------------------------------
    # embed_async
    # ------------------------------------------------------------------

    async def embed_async(self, text: str, is_query: bool = False) -> EmbedResult:
        async def _call() -> EmbedResult:
            if self._input_type == "text":
                client = self._get_async_openai_client()
                response = await client.embeddings.create(
                    input=text, model=self.model_name, dimensions=self._dimension
                )
                self._update_telemetry_token_usage(response)
                vector = response.data[0].embedding
            else:
                client = self._get_async_httpx_client()
                resp = await client.post(self._multimodal_url, json=self._multimodal_body(text))
                resp.raise_for_status()
                self._update_telemetry_token_usage(resp)
                vector = resp.json()["output"]["embeddings"][0]["embedding"]

            return EmbedResult(dense_vector=truncate_and_normalize(vector, self._dimension))

        try:
            return await self._run_with_async_retry(
                _call, logger=logger, operation_name="DashScope async embedding"
            )
        except Exception as e:
            raise RuntimeError(f"DashScope embedding failed: {e}") from e

    # ------------------------------------------------------------------
    # embed_batch — text mode chunks by 10 (DashScope text API limit)
    # ------------------------------------------------------------------

    def embed_batch(self, texts: List[str], is_query: bool = False) -> List[EmbedResult]:
        if not texts:
            return []

        def _call() -> List[EmbedResult]:
            results: List[EmbedResult] = []
            if self._input_type == "text":
                for i in range(0, len(texts), 10):
                    batch = texts[i : i + 10]
                    response = self._openai_client.embeddings.create(
                        input=batch, model=self.model_name, dimensions=self._dimension
                    )
                    self._update_telemetry_token_usage(response)
                    for item in response.data:
                        results.append(
                            EmbedResult(
                                dense_vector=truncate_and_normalize(item.embedding, self._dimension)
                            )
                        )
            else:
                for text in texts:
                    resp = self._httpx_client.post(
                        self._multimodal_url, json=self._multimodal_body(text)
                    )
                    resp.raise_for_status()
                    self._update_telemetry_token_usage(resp)
                    vector = resp.json()["output"]["embeddings"][0]["embedding"]
                    results.append(
                        EmbedResult(dense_vector=truncate_and_normalize(vector, self._dimension))
                    )
            return results

        try:
            return self._run_with_retry(
                _call, logger=logger, operation_name="DashScope batch embedding"
            )
        except Exception as e:
            logger.error(
                f"DashScope batch embedding failed, texts length: {len(texts)}, "
                f"input_type: {self._input_type}, model_name: {self.model_name}"
            )
            raise RuntimeError(f"DashScope batch embedding failed: {e}") from e

    # ------------------------------------------------------------------
    # embed_batch_async
    # ------------------------------------------------------------------

    async def embed_batch_async(
        self, texts: List[str], is_query: bool = False
    ) -> List[EmbedResult]:
        if not texts:
            return []

        async def _call() -> List[EmbedResult]:
            results: List[EmbedResult] = []
            if self._input_type == "text":
                client = self._get_async_openai_client()
                for i in range(0, len(texts), 10):
                    batch = texts[i : i + 10]
                    response = await client.embeddings.create(
                        input=batch, model=self.model_name, dimensions=self._dimension
                    )
                    self._update_telemetry_token_usage(response)
                    for item in response.data:
                        results.append(
                            EmbedResult(
                                dense_vector=truncate_and_normalize(item.embedding, self._dimension)
                            )
                        )
            else:
                client = self._get_async_httpx_client()
                for text in texts:
                    resp = await client.post(self._multimodal_url, json=self._multimodal_body(text))
                    resp.raise_for_status()
                    self._update_telemetry_token_usage(resp)
                    vector = resp.json()["output"]["embeddings"][0]["embedding"]
                    results.append(
                        EmbedResult(dense_vector=truncate_and_normalize(vector, self._dimension))
                    )
            return results

        try:
            return await self._run_with_async_retry(
                _call, logger=logger, operation_name="DashScope async batch embedding"
            )
        except Exception as e:
            logger.error(
                f"DashScope async batch embedding failed, texts length: {len(texts)}, "
                f"input_type: {self._input_type}, model_name: {self.model_name}"
            )
            raise RuntimeError(f"DashScope batch embedding failed: {e}") from e

    # ------------------------------------------------------------------
    # embed_content — multimodal content (text + image URLs)
    # ------------------------------------------------------------------

    def embed_content(self, contents: List[Dict[str, str]]) -> EmbedResult:
        """Embed multimodal content (text + image URLs) via native DashScope API.

        Args:
            contents: List of content dicts, e.g. [{"text": "描述"}, {"image": "https://..."}]

        Returns:
            EmbedResult with dense_vector
        """

        def _call() -> EmbedResult:
            body: Dict[str, Any] = {
                "model": self.model_name,
                "input": {"contents": contents},
            }
            params = self._multimodal_params()
            if params:
                body["parameters"] = params
            resp = self._httpx_client.post(self._multimodal_url, json=body)
            resp.raise_for_status()
            self._update_telemetry_token_usage(resp)
            vector = resp.json()["output"]["embeddings"][0]["embedding"]
            return EmbedResult(dense_vector=truncate_and_normalize(vector, self._dimension))

        try:
            return self._run_with_retry(
                _call, logger=logger, operation_name="DashScope content embedding"
            )
        except Exception as e:
            raise RuntimeError(f"DashScope content embedding failed: {e}") from e

    async def embed_content_async(self, contents: List[Dict[str, str]]) -> EmbedResult:
        """Async version of embed_content."""

        client = self._get_async_httpx_client()

        async def _call() -> EmbedResult:
            body: Dict[str, Any] = {
                "model": self.model_name,
                "input": {"contents": contents},
            }
            params = self._multimodal_params()
            if params:
                body["parameters"] = params
            resp = await client.post(self._multimodal_url, json=body)
            resp.raise_for_status()
            self._update_telemetry_token_usage(resp)
            vector = resp.json()["output"]["embeddings"][0]["embedding"]
            return EmbedResult(dense_vector=truncate_and_normalize(vector, self._dimension))

        try:
            return await self._run_with_async_retry(
                _call, logger=logger, operation_name="DashScope async content embedding"
            )
        except Exception as e:
            raise RuntimeError(f"DashScope content embedding failed: {e}") from e

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def get_dimension(self) -> int:
        return self._dimension

    def close(self):
        # --- sync clients ---
        if self._openai_client is not None:
            self._openai_client.close()
        if self._httpx_client is not None:
            self._httpx_client.close()
        # --- async clients (event-loop-aware cleanup) ---
        import asyncio

        async def _close_async_clients() -> None:
            if self._async_openai_client is not None:
                await self._async_openai_client.close()
            if self._async_httpx_client is not None:
                await self._async_httpx_client.aclose()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            loop.create_task(_close_async_clients())
        else:
            asyncio.run(_close_async_clients())
