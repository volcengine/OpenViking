# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""VikingDB Embedder Implementation via HTTP API"""

import asyncio
from typing import Any, Dict, List, Optional

import httpx

from openviking.models.embedder.base import (
    DenseEmbedderBase,
    EmbedResult,
    HybridEmbedderBase,
    SparseEmbedderBase,
)
from openviking.storage.vectordb.collection.volcengine_clients import (
    DEFAULT_TIMEOUT,
    ClientForDataApi,
)
from openviking_cli.utils.logger import default_logger as logger


class VikingDBClientMixin:
    """Mixin to handle VikingDB Client initialization and API calls."""

    def _init_vikingdb_client(
        self,
        ak: Optional[str] = None,
        sk: Optional[str] = None,
        region: Optional[str] = None,
        host: Optional[str] = None,
    ):
        self.ak = ak
        self.sk = sk
        self.region = region or "cn-beijing"
        self.host = host

        if not self.ak or not self.sk:
            raise ValueError("AK and SK are required for VikingDB Embedder")

        self.client = ClientForDataApi(self.ak, self.sk, self.region, self.host)
        self._async_client: Optional[httpx.AsyncClient] = None

    def _call_api(
        self,
        texts: List[str],
        dense_model: Dict[str, Any] = None,
        sparse_model: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Call VikingDB Embedding API"""
        path = "/api/vikingdb/embedding"

        data_items = [{"text": text} for text in texts]

        req_body = {"data": data_items}
        if dense_model:
            req_body["dense_model"] = dense_model
        if sparse_model:
            req_body["sparse_model"] = sparse_model

        try:
            response = self.client.do_req("POST", path, req_body=req_body)
            if response.status_code != 200:
                logger.warning(
                    f"VikingDB API returned bad code: {response.status_code}, message: {response.text}"
                )
                return []

            result = response.json()
            return result.get("result", {}).get("data", [])

        except Exception as e:
            logger.error(f"Failed to get embeddings: {e}")
            raise e

    async def _call_api_async(
        self,
        texts: List[str],
        dense_model: Dict[str, Any] = None,
        sparse_model: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        path = "/api/vikingdb/embedding"
        data_items = [{"text": text} for text in texts]

        req_body = {"data": data_items}
        if dense_model:
            req_body["dense_model"] = dense_model
        if sparse_model:
            req_body["sparse_model"] = sparse_model

        req = self.client.prepare_request(method="POST", path=path, data=req_body)
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)

        response = await self._async_client.request(
            method=req.method,
            url=f"https://{self.host}{req.path}",
            headers=req.headers,
            content=req.body,
        )
        if response.status_code != 200:
            logger.warning(
                "VikingDB API returned bad code: %s, message: %s",
                response.status_code,
                response.text,
            )
            return []

        result = response.json()
        return result.get("result", {}).get("data", [])

    def _truncate_and_normalize(
        self, embedding: List[float], dimension: Optional[int]
    ) -> List[float]:
        """Truncate and L2 normalize embedding"""
        if not dimension or len(embedding) <= dimension:
            return embedding

        import math

        embedding = embedding[:dimension]
        norm = math.sqrt(sum(x**2 for x in embedding))
        if norm > 0:
            embedding = [x / norm for x in embedding]
        return embedding

    def _process_sparse_embedding(self, sparse_data: Any) -> Dict[str, float]:
        """Process sparse embedding data"""
        if not sparse_data:
            return {}

        result = {}
        if isinstance(sparse_data, dict):
            return {str(k): float(v) for k, v in sparse_data.items()}

        if isinstance(sparse_data, list):
            for item in sparse_data:
                if isinstance(item, dict):
                    # Handle common formats
                    key = item.get("key") or item.get("index") or item.get("token")
                    val = item.get("value") or item.get("weight") or item.get("score")
                    if key is not None and val is not None:
                        result[str(key)] = float(val)
        return result


class VikingDBDenseEmbedder(DenseEmbedderBase, VikingDBClientMixin):
    """VikingDB Dense Embedder"""

    def __init__(
        self,
        model_name: str,
        model_version: Optional[str] = None,
        ak: Optional[str] = None,
        sk: Optional[str] = None,
        region: Optional[str] = None,
        host: Optional[str] = None,
        dimension: Optional[int] = None,
        embedding_type: str = "text",
        config: Optional[Dict[str, Any]] = None,
    ):
        DenseEmbedderBase.__init__(self, model_name, config)
        self._init_vikingdb_client(ak, sk, region, host)
        self.model_version = model_version
        self.dimension = dimension
        self.embedding_type = embedding_type
        self.dense_model = {"name": model_name, "version": model_version, "dim": dimension}

    def embed(self, text: str, is_query: bool = False) -> EmbedResult:
        def _call() -> EmbedResult:
            results = self._call_api([text], dense_model=self.dense_model)
            if not results:
                return EmbedResult(dense_vector=[])

            item = results[0]
            dense_vector = []
            if "dense_embedding" in item:
                dense_vector = self._truncate_and_normalize(item["dense_embedding"], self.dimension)

            return EmbedResult(dense_vector=dense_vector)

        result = self._run_with_retry(
            _call,
            logger=logger,
            operation_name="VikingDB embedding",
        )
        # Estimate token usage
        estimated_tokens = self._estimate_tokens(text)
        self.update_token_usage(
            model_name=self.model_name,
            provider="volcengine",
            prompt_tokens=estimated_tokens,
            completion_tokens=0,
        )
        return result

    def embed_batch(self, texts: List[str], is_query: bool = False) -> List[EmbedResult]:
        if not texts:
            return []

        def _call() -> List[EmbedResult]:
            raw_results = self._call_api(texts, dense_model=self.dense_model)
            return [
                EmbedResult(
                    dense_vector=self._truncate_and_normalize(
                        item.get("dense_embedding", []), self.dimension
                    )
                )
                for item in raw_results
            ]

        results = self._run_with_retry(
            _call,
            logger=logger,
            operation_name="VikingDB batch embedding",
        )
        # Estimate token usage for batch
        total_tokens = sum(self._estimate_tokens(text) for text in texts)
        self.update_token_usage(
            model_name=self.model_name,
            provider="volcengine",
            prompt_tokens=total_tokens,
            completion_tokens=0,
        )
        return results

    async def embed_async(self, text: str, is_query: bool = False) -> EmbedResult:
        async def _call() -> EmbedResult:
            results = await self._call_api_async([text], dense_model=self.dense_model)
            if not results:
                return EmbedResult(dense_vector=[])

            item = results[0]
            dense_vector = []
            if "dense_embedding" in item:
                dense_vector = self._truncate_and_normalize(item["dense_embedding"], self.dimension)
            return EmbedResult(dense_vector=dense_vector)

        result = await self._run_with_async_retry(
            _call,
            logger=logger,
            operation_name="VikingDB async embedding",
        )
        estimated_tokens = self._estimate_tokens(text)
        self.update_token_usage(
            model_name=self.model_name,
            provider="volcengine",
            prompt_tokens=estimated_tokens,
            completion_tokens=0,
        )
        return result

    async def embed_batch_async(
        self, texts: List[str], is_query: bool = False
    ) -> List[EmbedResult]:
        if not texts:
            return []

        async def _call() -> List[EmbedResult]:
            raw_results = await self._call_api_async(texts, dense_model=self.dense_model)
            return [
                EmbedResult(
                    dense_vector=self._truncate_and_normalize(
                        item.get("dense_embedding", []), self.dimension
                    )
                )
                for item in raw_results
            ]

        results = await self._run_with_async_retry(
            _call,
            logger=logger,
            operation_name="VikingDB async batch embedding",
        )
        total_tokens = sum(self._estimate_tokens(text) for text in texts)
        self.update_token_usage(
            model_name=self.model_name,
            provider="volcengine",
            prompt_tokens=total_tokens,
            completion_tokens=0,
        )
        return results

    def get_dimension(self) -> int:
        return self.dimension if self.dimension else 2048


class VikingDBSparseEmbedder(SparseEmbedderBase, VikingDBClientMixin):
    """VikingDB Sparse Embedder"""

    def __init__(
        self,
        model_name: str,
        model_version: Optional[str] = None,
        ak: Optional[str] = None,
        sk: Optional[str] = None,
        region: Optional[str] = None,
        host: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        SparseEmbedderBase.__init__(self, model_name, config)
        self._init_vikingdb_client(ak, sk, region, host)
        self.model_version = model_version
        self.sparse_model = {
            "name": model_name,
            "version": model_version,
        }

    def embed(self, text: str, is_query: bool = False) -> EmbedResult:
        def _call() -> EmbedResult:
            results = self._call_api([text], sparse_model=self.sparse_model)
            if not results:
                return EmbedResult(sparse_vector={})

            item = results[0]
            sparse_vector = {}
            if "sparse" in item:
                sparse_vector = item["sparse"]

            return EmbedResult(sparse_vector=sparse_vector)

        result = self._run_with_retry(
            _call,
            logger=logger,
            operation_name="VikingDB sparse embedding",
        )
        # Estimate token usage
        estimated_tokens = self._estimate_tokens(text)
        self.update_token_usage(
            model_name=self.model_name,
            provider="volcengine",
            prompt_tokens=estimated_tokens,
            completion_tokens=0,
        )
        return result

    def embed_batch(self, texts: List[str], is_query: bool = False) -> List[EmbedResult]:
        if not texts:
            return []

        def _call() -> List[EmbedResult]:
            raw_results = self._call_api(texts, sparse_model=self.sparse_model)
            return [
                EmbedResult(
                    sparse_vector=self._process_sparse_embedding(item.get("sparse_embedding", {}))
                )
                for item in raw_results
            ]

        results = self._run_with_retry(
            _call,
            logger=logger,
            operation_name="VikingDB sparse batch embedding",
        )
        # Estimate token usage for batch
        total_tokens = sum(self._estimate_tokens(text) for text in texts)
        self.update_token_usage(
            model_name=self.model_name,
            provider="volcengine",
            prompt_tokens=total_tokens,
            completion_tokens=0,
        )
        return results

    async def embed_async(self, text: str, is_query: bool = False) -> EmbedResult:
        async def _call() -> EmbedResult:
            results = await self._call_api_async([text], sparse_model=self.sparse_model)
            if not results:
                return EmbedResult(sparse_vector={})

            item = results[0]
            sparse_vector = {}
            if "sparse" in item:
                sparse_vector = item["sparse"]
            elif "sparse_embedding" in item:
                sparse_vector = self._process_sparse_embedding(item["sparse_embedding"])
            return EmbedResult(sparse_vector=sparse_vector)

        result = await self._run_with_async_retry(
            _call,
            logger=logger,
            operation_name="VikingDB async sparse embedding",
        )
        estimated_tokens = self._estimate_tokens(text)
        self.update_token_usage(
            model_name=self.model_name,
            provider="volcengine",
            prompt_tokens=estimated_tokens,
            completion_tokens=0,
        )
        return result

    async def embed_batch_async(
        self, texts: List[str], is_query: bool = False
    ) -> List[EmbedResult]:
        if not texts:
            return []

        async def _call() -> List[EmbedResult]:
            raw_results = await self._call_api_async(texts, sparse_model=self.sparse_model)
            return [
                EmbedResult(
                    sparse_vector=self._process_sparse_embedding(
                        item.get("sparse_embedding", item.get("sparse", {}))
                    )
                )
                for item in raw_results
            ]

        results = await self._run_with_async_retry(
            _call,
            logger=logger,
            operation_name="VikingDB async sparse batch embedding",
        )
        total_tokens = sum(self._estimate_tokens(text) for text in texts)
        self.update_token_usage(
            model_name=self.model_name,
            provider="volcengine",
            prompt_tokens=total_tokens,
            completion_tokens=0,
        )
        return results


class VikingDBHybridEmbedder(HybridEmbedderBase, VikingDBClientMixin):
    """VikingDB Hybrid Embedder"""

    def __init__(
        self,
        model_name: str,
        model_version: Optional[str] = None,
        ak: Optional[str] = None,
        sk: Optional[str] = None,
        region: Optional[str] = None,
        host: Optional[str] = None,
        dimension: Optional[int] = None,
        embedding_type: str = "text",
        config: Optional[Dict[str, Any]] = None,
    ):
        HybridEmbedderBase.__init__(self, model_name, config)
        self._init_vikingdb_client(ak, sk, region, host)
        self.model_version = model_version
        self.dimension = dimension
        self.embedding_type = embedding_type
        self.dense_model = {"name": model_name, "version": model_version, "dim": dimension}
        self.sparse_model = {
            "name": model_name,
            "version": model_version,
        }

    def embed(self, text: str, is_query: bool = False) -> EmbedResult:
        def _call() -> EmbedResult:
            results = self._call_api(
                [text], dense_model=self.dense_model, sparse_model=self.sparse_model
            )
            if not results:
                return EmbedResult(dense_vector=[], sparse_vector={})

            item = results[0]
            dense_vector = []
            sparse_vector = {}

            if "dense" in item:
                dense_vector = self._truncate_and_normalize(item["dense"], self.dimension)
            if "sparse" in item:
                sparse_vector = item["sparse"]

            return EmbedResult(dense_vector=dense_vector, sparse_vector=sparse_vector)

        result = self._run_with_retry(
            _call,
            logger=logger,
            operation_name="VikingDB hybrid embedding",
        )
        # Estimate token usage
        estimated_tokens = self._estimate_tokens(text)
        self.update_token_usage(
            model_name=self.model_name,
            provider="volcengine",
            prompt_tokens=estimated_tokens,
            completion_tokens=0,
        )
        return result

    def embed_batch(self, texts: List[str], is_query: bool = False) -> List[EmbedResult]:
        if not texts:
            return []

        def _call() -> List[EmbedResult]:
            raw_results = self._call_api(
                texts, dense_model=self.dense_model, sparse_model=self.sparse_model
            )
            results = []
            for item in raw_results:
                dense_vector = []
                sparse_vector = {}
                if "dense" in item:
                    dense_vector = self._truncate_and_normalize(item["dense"], self.dimension)
                if "sparse" in item:
                    sparse_vector = item["sparse"]
                results.append(EmbedResult(dense_vector=dense_vector, sparse_vector=sparse_vector))
            return results

        results = self._run_with_retry(
            _call,
            logger=logger,
            operation_name="VikingDB hybrid batch embedding",
        )
        # Estimate token usage for batch
        total_tokens = sum(self._estimate_tokens(text) for text in texts)
        self.update_token_usage(
            model_name=self.model_name,
            provider="volcengine",
            prompt_tokens=total_tokens,
            completion_tokens=0,
        )
        return results

    async def embed_async(self, text: str, is_query: bool = False) -> EmbedResult:
        async def _call() -> EmbedResult:
            results = await self._call_api_async(
                [text], dense_model=self.dense_model, sparse_model=self.sparse_model
            )
            if not results:
                return EmbedResult(dense_vector=[], sparse_vector={})

            item = results[0]
            dense_vector = []
            sparse_vector = {}
            if "dense" in item:
                dense_vector = self._truncate_and_normalize(item["dense"], self.dimension)
            elif "dense_embedding" in item:
                dense_vector = self._truncate_and_normalize(item["dense_embedding"], self.dimension)
            if "sparse" in item:
                sparse_vector = item["sparse"]
            elif "sparse_embedding" in item:
                sparse_vector = self._process_sparse_embedding(item["sparse_embedding"])
            return EmbedResult(dense_vector=dense_vector, sparse_vector=sparse_vector)

        result = await self._run_with_async_retry(
            _call,
            logger=logger,
            operation_name="VikingDB async hybrid embedding",
        )
        estimated_tokens = self._estimate_tokens(text)
        self.update_token_usage(
            model_name=self.model_name,
            provider="volcengine",
            prompt_tokens=estimated_tokens,
            completion_tokens=0,
        )
        return result

    async def embed_batch_async(
        self, texts: List[str], is_query: bool = False
    ) -> List[EmbedResult]:
        if not texts:
            return []

        async def _call() -> List[EmbedResult]:
            raw_results = await self._call_api_async(
                texts, dense_model=self.dense_model, sparse_model=self.sparse_model
            )
            results = []
            for item in raw_results:
                dense_vector = []
                sparse_vector = {}
                if "dense" in item:
                    dense_vector = self._truncate_and_normalize(item["dense"], self.dimension)
                elif "dense_embedding" in item:
                    dense_vector = self._truncate_and_normalize(
                        item["dense_embedding"], self.dimension
                    )
                if "sparse" in item:
                    sparse_vector = item["sparse"]
                elif "sparse_embedding" in item:
                    sparse_vector = self._process_sparse_embedding(item["sparse_embedding"])
                results.append(EmbedResult(dense_vector=dense_vector, sparse_vector=sparse_vector))
            return results

        results = await self._run_with_async_retry(
            _call,
            logger=logger,
            operation_name="VikingDB async hybrid batch embedding",
        )
        total_tokens = sum(self._estimate_tokens(text) for text in texts)
        self.update_token_usage(
            model_name=self.model_name,
            provider="volcengine",
            prompt_tokens=total_tokens,
            completion_tokens=0,
        )
        return results

    def get_dimension(self) -> int:
        return self.dimension if self.dimension else 2048

    def close(self):
        if getattr(self, "_async_client", None) is not None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop and loop.is_running():
                loop.create_task(self._async_client.aclose())
            else:
                asyncio.run(self._async_client.aclose())
