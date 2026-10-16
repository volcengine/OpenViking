# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""VikingDB embedding client using one model for all requested vector output."""

from typing import Any, Dict, Optional

import httpx

from openviking.models.embedder.base import EmbedderBase, EmbeddingInput, EmbedResult
from openviking.models.model_error import model_error_boundary
from openviking.storage.vectordb.collection.volcengine_clients import (
    DEFAULT_TIMEOUT,
    ClientForDataApi,
)
from openviking.utils.async_client_cache import LoopScopedAsyncClientCache
from openviking_cli.utils.logger import default_logger as logger


class VikingDBEmbedder(EmbedderBase):
    """Generate dense or dense+sparse vectors in a single VikingDB request."""

    def __init__(
        self,
        model_name: str,
        model_version: Optional[str] = None,
        ak: Optional[str] = None,
        sk: Optional[str] = None,
        region: Optional[str] = None,
        host: Optional[str] = None,
        dimension: Optional[int] = None,
        output: str = "dense",
        config: Optional[Dict[str, Any]] = None,
        query_param: Optional[str] = None,
        document_param: Optional[str] = None,
    ):
        super().__init__(model_name, config, output=output)
        self.provider = "vikingdb"
        self.client = ClientForDataApi(ak, sk, region or "cn-beijing", host)
        self._async_client_cache = LoopScopedAsyncClientCache()
        self.model_version = model_version
        self.dimension = dimension
        self.query_param = query_param
        self.document_param = document_param

    def _request_body(self, content: EmbeddingInput, is_query: bool) -> dict:
        text = self.prepare_embedding_input(content)
        data = {"text": text}
        input_type = self.query_param if is_query else self.document_param
        if input_type is not None:
            data["input_type"] = input_type
        model = {"name": self.model_name}
        if self.model_version is not None:
            model["version"] = self.model_version
        dense_model = dict(model)
        if self.dimension is not None:
            dense_model["dim"] = self.dimension
        body = {"data": [data], "dense_model": dense_model}
        if self.output == "dense_sparse":
            body["sparse_model"] = model
        return body

    def _parse_response(self, response) -> EmbedResult:
        response.raise_for_status()
        payload = response.json()
        if payload["code"] != "Success":
            raise RuntimeError(
                f"VikingDB embedding failed: {payload['code']}: {payload['message']}"
            )
        result = payload["result"]
        item = result["data"][0]
        usage = result["token_usage"]
        self.update_token_usage(
            model_name=self.model_name,
            provider=self.provider,
            prompt_tokens=sum(item["prompt_tokens"] for item in usage.values()),
            completion_tokens=sum(item["completion_tokens"] for item in usage.values()),
        )
        return EmbedResult(
            dense_vector=item["dense"],
            sparse_vector=item["sparse"] if self.output == "dense_sparse" else None,
        )

    @model_error_boundary
    def embed(self, content: EmbeddingInput, is_query: bool = False) -> EmbedResult:
        body = self._request_body(content, is_query)
        return self._run_once(
            lambda: self._parse_response(
                self.client.do_req("POST", "/api/vikingdb/embedding", req_body=body)
            ),
            logger=logger,
            operation_name="VikingDB embedding",
        )

    @model_error_boundary
    async def embed_async(self, content: EmbeddingInput, is_query: bool = False) -> EmbedResult:
        body = self._request_body(content, is_query)
        request = self.client.prepare_request(
            method="POST", path="/api/vikingdb/embedding", data=body
        )
        client = self._async_client_cache.get(lambda: httpx.AsyncClient(timeout=DEFAULT_TIMEOUT))

        async def call() -> EmbedResult:
            response = await client.request(
                method=request.method,
                url=f"https://{self.client.host}{request.path}",
                headers=request.headers,
                content=request.body,
            )
            return self._parse_response(response)

        return await self._run_once_async(
            call, logger=logger, operation_name="VikingDB async embedding"
        )

    def get_dimension(self) -> int:
        return self.dimension if self.dimension is not None else 2048

    def close(self):
        self.client._session.close()
        self._async_client_cache.close_all_with_aclose()
