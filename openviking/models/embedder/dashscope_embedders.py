# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""DashScope (Alibaba Tongyi) Embedder Implementation"""

import asyncio
from typing import Any, Dict, List, Optional

import httpx

from openviking.models.embedder.base import (
    DenseEmbedderBase,
    EmbedResult,
    truncate_and_normalize,
)
from openviking_cli.utils.logger import default_logger as logger

DEFAULT_CN_ENDPOINT = "https://dashscope.aliyuncs.com"
DEFAULT_INTL_ENDPOINT = "https://dashscope-intl.aliyuncs.com"
_TEXT_EMBEDDINGS_PATH = "/compatible-mode/v1/embeddings"
_MULTIMODAL_EMBEDDINGS_PATH = (
    "/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"
)

DASHSCOPE_MODEL_DIMENSIONS: Dict[str, int] = {
    "qwen3-vl-embedding": 2560,
    "qwen2.5-vl-embedding": 1024,
    "tongyi-embedding-vision-plus-2026-03-06": 1152,
    "tongyi-embedding-vision-flash-2026-03-06": 768,
    "tongyi-embedding-vision-plus": 1152,
    "tongyi-embedding-vision-flash": 768,
    "text-embedding-v1": 1536,
    "text-embedding-v2": 1536,
    "text-embedding-v3": 1024,
    "text-embedding-v4": 1024,
}


def get_dashscope_model_default_dimension(model_name: Optional[str]) -> int:
    if not model_name:
        return 1024
    return DASHSCOPE_MODEL_DIMENSIONS.get(model_name, 1024)


def _resolve_endpoint(endpoint: Optional[str]) -> str:
    if not endpoint:
        return DEFAULT_CN_ENDPOINT
    value = endpoint.strip().lower()
    if value in ("cn", "china", "default"):
        return DEFAULT_CN_ENDPOINT
    if value in ("intl", "international", "global"):
        return DEFAULT_INTL_ENDPOINT
    return endpoint.rstrip("/")


class DashScopeDenseEmbedder(DenseEmbedderBase):
    """DashScope (Alibaba Tongyi) Dense Embedder Implementation.

    Supports DashScope embedding models via two routes:
    - Text mode: OpenAI-compatible endpoint at /compatible-mode/v1/embeddings.
    - Multimodal mode: native /api/v1/services/embeddings/multimodal-embedding/...

    Multimodal mode is selected via ``input_type="multimodal"`` (default), matching
    volcengine_embedders. Set ``input_type="text"`` to force the OpenAI-compatible
    route. Endpoint selection supports the CN (default) and international (``intl``)
    DashScope hosts.
    """

    def __init__(
        self,
        model_name: str,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        endpoint: Optional[str] = None,
        dimension: Optional[int] = None,
        input_type: str = "multimodal",
        enable_fusion: Optional[bool] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(model_name, config)
        self.provider = "dashscope"

        self.api_key = api_key
        self.input_type = input_type
        self.enable_fusion = enable_fusion
        self.dimension = dimension

        if not self.api_key:
            raise ValueError("api_key is required")

        resolved_base = api_base.rstrip("/") if api_base else _resolve_endpoint(endpoint)
        self.api_base = resolved_base
        self._text_url = f"{resolved_base}{_TEXT_EMBEDDINGS_PATH}"
        self._multimodal_url = f"{resolved_base}{_MULTIMODAL_EMBEDDINGS_PATH}"

        self._client: Optional[httpx.Client] = None
        self._async_client: Optional[httpx.AsyncClient] = None

        self._dimension = dimension or get_dashscope_model_default_dimension(model_name)

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=60.0, headers=self._build_headers())
        return self._client

    def _get_async_client(self) -> httpx.AsyncClient:
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(timeout=60.0, headers=self._build_headers())
        return self._async_client

    def _build_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _is_multimodal(self) -> bool:
        return (self.input_type or "").lower() == "multimodal"

    def _build_text_payload(self, texts: List[str]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"model": self.model_name, "input": texts}
        if self.dimension:
            payload["dimensions"] = self.dimension
        return payload

    def _build_multimodal_payload(self, texts: List[str]) -> Dict[str, Any]:
        contents = [{"text": text} for text in texts]
        parameters: Dict[str, Any] = {}
        if self.dimension:
            parameters["dimension"] = self.dimension
        if self.enable_fusion is not None:
            parameters["enable_fusion"] = bool(self.enable_fusion)
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "input": {"contents": contents},
        }
        if parameters:
            payload["parameters"] = parameters
        return payload

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        status = response.status_code
        try:
            body = response.json()
        except Exception:
            body = response.text
        if status == 401:
            raise RuntimeError(f"DashScope API error 401: invalid api_key ({body})")
        if status == 400:
            raise RuntimeError(f"DashScope API error 400: bad request ({body})")
        raise RuntimeError(f"DashScope API error {status}: {body}")

    def _parse_text_response(self, data: Dict[str, Any]) -> List[List[float]]:
        items = data.get("data")
        if not items:
            raise RuntimeError(f"DashScope text response missing 'data': {data}")
        return [item["embedding"] for item in items]

    def _parse_multimodal_response(self, data: Dict[str, Any]) -> List[List[float]]:
        output = data.get("output")
        if output is None:
            message = data.get("message") or data.get("code") or data
            raise RuntimeError(f"DashScope multimodal response missing 'output': {message}")
        embeddings = output.get("embeddings")
        if not embeddings:
            raise RuntimeError(f"DashScope multimodal response missing 'embeddings': {output}")
        vectors: List[List[float]] = []
        for item in embeddings:
            vec = item.get("embedding")
            if vec is None:
                raise RuntimeError(f"DashScope multimodal embedding entry missing vector: {item}")
            vectors.append(vec)
        return vectors

    def _call_text(self, texts: List[str]) -> List[List[float]]:
        client = self._get_client()
        response = client.post(self._text_url, json=self._build_text_payload(texts))
        self._raise_for_status(response)
        return self._parse_text_response(response.json())

    def _call_multimodal(self, texts: List[str]) -> List[List[float]]:
        client = self._get_client()
        response = client.post(
            self._multimodal_url, json=self._build_multimodal_payload(texts)
        )
        self._raise_for_status(response)
        return self._parse_multimodal_response(response.json())

    async def _call_text_async(self, texts: List[str]) -> List[List[float]]:
        client = self._get_async_client()
        response = await client.post(self._text_url, json=self._build_text_payload(texts))
        self._raise_for_status(response)
        return self._parse_text_response(response.json())

    async def _call_multimodal_async(self, texts: List[str]) -> List[List[float]]:
        client = self._get_async_client()
        response = await client.post(
            self._multimodal_url, json=self._build_multimodal_payload(texts)
        )
        self._raise_for_status(response)
        return self._parse_multimodal_response(response.json())

    def _normalize(self, vectors: List[List[float]]) -> List[List[float]]:
        return [truncate_and_normalize(v, self.dimension) for v in vectors]

    def embed(self, text: str, is_query: bool = False) -> EmbedResult:
        def _call() -> EmbedResult:
            if self._is_multimodal():
                vectors = self._call_multimodal([text])
            else:
                vectors = self._call_text([text])
            return EmbedResult(dense_vector=self._normalize(vectors)[0])

        try:
            result = self._run_with_retry(
                _call,
                logger=logger,
                operation_name="DashScope embedding",
            )
            self.update_token_usage(
                model_name=self.model_name,
                provider="dashscope",
                prompt_tokens=self._estimate_tokens(text),
                completion_tokens=0,
            )
            return result
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"DashScope embedding failed: {e}") from e

    async def embed_async(self, text: str, is_query: bool = False) -> EmbedResult:
        async def _call() -> EmbedResult:
            if self._is_multimodal():
                vectors = await self._call_multimodal_async([text])
            else:
                vectors = await self._call_text_async([text])
            return EmbedResult(dense_vector=self._normalize(vectors)[0])

        try:
            result = await self._run_with_async_retry(
                _call,
                logger=logger,
                operation_name="DashScope async embedding",
            )
            self.update_token_usage(
                model_name=self.model_name,
                provider="dashscope",
                prompt_tokens=self._estimate_tokens(text),
                completion_tokens=0,
            )
            return result
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"DashScope embedding failed: {e}") from e

    def embed_batch(self, texts: List[str], is_query: bool = False) -> List[EmbedResult]:
        if not texts:
            return []

        def _call() -> List[EmbedResult]:
            if self._is_multimodal():
                return [self.embed(text, is_query=is_query) for text in texts]
            vectors = self._call_text(texts)
            return [EmbedResult(dense_vector=v) for v in self._normalize(vectors)]

        try:
            results = self._run_with_retry(
                _call,
                logger=logger,
                operation_name="DashScope batch embedding",
            )
            if not self._is_multimodal():
                total_tokens = sum(self._estimate_tokens(text) for text in texts)
                self.update_token_usage(
                    model_name=self.model_name,
                    provider="dashscope",
                    prompt_tokens=total_tokens,
                    completion_tokens=0,
                )
            return results
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"DashScope batch embedding failed: {e}") from e

    async def embed_batch_async(
        self, texts: List[str], is_query: bool = False
    ) -> List[EmbedResult]:
        if not texts:
            return []

        async def _call() -> List[EmbedResult]:
            if self._is_multimodal():
                return list(
                    await asyncio.gather(
                        *[self.embed_async(text, is_query=is_query) for text in texts]
                    )
                )
            vectors = await self._call_text_async(texts)
            return [EmbedResult(dense_vector=v) for v in self._normalize(vectors)]

        try:
            results = await self._run_with_async_retry(
                _call,
                logger=logger,
                operation_name="DashScope async batch embedding",
            )
            if not self._is_multimodal():
                total_tokens = sum(self._estimate_tokens(text) for text in texts)
                self.update_token_usage(
                    model_name=self.model_name,
                    provider="dashscope",
                    prompt_tokens=total_tokens,
                    completion_tokens=0,
                )
            return results
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"DashScope batch embedding failed: {e}") from e

    def get_dimension(self) -> int:
        return self._dimension

    def close(self):
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
        if self._async_client is not None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop and loop.is_running():
                loop.create_task(self._async_client.aclose())
            else:
                asyncio.run(self._async_client.aclose())
