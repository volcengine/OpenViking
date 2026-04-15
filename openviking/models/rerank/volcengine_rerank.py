# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
VikingDB Rerank API Client.

Provides rerank functionality for hierarchical retrieval.
"""

import json

# For logging, use Python's built-in logging
import logging
from typing import List, Optional

import requests
from volcengine.auth.SignerV4 import SignerV4
from volcengine.base.Request import Request
from volcengine.Credentials import Credentials

from openviking.models.rerank.base import RerankBase

logger = logging.getLogger(__name__)


class RerankClient(RerankBase):
    """
    VikingDB Rerank API client.

    Supports batch rerank for multiple documents against a query.
    """

    def __init__(
        self,
        ak: str,
        sk: str,
        host: str = "api-vikingdb.vikingdb.cn-beijing.volces.com",
        model_name: str = "doubao-seed-rerank",
        model_version: str = "251028",
    ):
        """
        Initialize rerank client.

        Args:
            ak: VikingDB Access Key
            sk: VikingDB Secret Key
            host: VikingDB API host
            model_name: Rerank model name
            model_version: Rerank model version
        """
        super().__init__()
        self.ak = ak
        self.sk = sk
        self.host = host
        self.model_name = model_name
        self.model_version = model_version
        self.provider = "vikingdb"

    def _prepare_request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        data: Optional[dict] = None,
    ) -> Request:
        """Prepare signed request for VikingDB API."""
        r = Request()
        r.set_shema("https")
        r.set_method(method)
        r.set_connection_timeout(10)
        r.set_socket_timeout(30)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Host": self.host,
        }
        r.set_headers(headers)
        if params:
            r.set_query(params)
        r.set_host(self.host)
        r.set_path(path)
        if data is not None:
            r.set_body(json.dumps(data))
        credentials = Credentials(self.ak, self.sk, "vikingdb", "cn-beijing")
        SignerV4.sign(r, credentials)
        return r

    def rerank_batch(self, query: str, documents: List[str]) -> Optional[List[float]]:
        """
        Batch rerank documents against a query.

        Args:
            query: Query text
            documents: List of document texts to rank

        Returns:
            List of rerank scores for each document (same order as input),
            or None when rerankver fails and the caller should fall back
        """
        if not documents:
            return []

        # Build request body
        req_body = {
            "model_name": self.model_name,
            "model_version": self.model_version,
            "data": [[{"text": doc}] for doc in documents],
            "query": [{"text": query}],
            "instruction": "Whether the Document answers the Query or matches the content retrieval intent",
        }

        try:
            req = self._prepare_request(
                method="POST",
                path="/api/vikingdb/rerank",
                data=req_body,
            )

            response = requests.request(
                method=req.method,
                url=f"https://{self.host}{req.path}",
                headers=req.headers,
                data=req.body,
                timeout=30,
            )

            result = response.json()
            # print(f"[RerankClient] Raw response: {result}")
            if "result" not in result or "data" not in result["result"]:
                logger.warning(f"[RerankClient] Unexpected response format: {result}")
                return None

            # Update token usage tracking (estimate, VikingDB doesn't provide token info)
            self._extract_and_update_token_usage(result, query, documents)

            # Each document is a separate group, data array returns scores for each group sequentially
            data = result["result"]["data"]
            if len(data) != len(documents):
                logger.warning(
                    "[RerankClient] Unexpected rerank result length: expected=%s actual=%s",
                    len(documents),
                    len(data),
                )
                return None
            scores = [item.get("score", 0.0) for item in data]

            logger.debug(f"[RerankClient] Reranked {len(documents)} documents")
            return scores

        except Exception as e:
            logger.error(f"[RerankClient] Rerank failed: {e}")
            return None

    @classmethod
    def from_config(cls, config) -> Optional["RerankClient"]:
        """
        Create RerankClient from RerankConfig.

        Args:
            config: RerankConfig instance

        Returns:
            RerankClient instance or None if config is not available
        """
        if not config or not config.is_available():
            return None

        provider = config._effective_provider()

        if provider == "cohere":
            from openviking.models.rerank.cohere_rerank import CohereRerankClient

            return CohereRerankClient.from_config(config)

        if provider == "litellm":
            from openviking.models.rerank.litellm_rerank import LiteLLMRerankClient

            return LiteLLMRerankClient.from_config(config)

        if provider == "openai":
            from openviking.models.rerank.openai_rerank import OpenAIRerankClient

            return OpenAIRerankClient.from_config(config)

        return cls(
            ak=config.ak,
            sk=config.sk,
            host=config.host,
            model_name=config.model_name,
            model_version=config.model_version,
        )
