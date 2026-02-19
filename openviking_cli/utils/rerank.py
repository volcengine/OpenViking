# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
VikingDB Rerank API Client.

Provides rerank functionality for hierarchical retrieval.
"""

import json
from typing import List, Optional

import requests
from volcengine.auth.SignerV4 import SignerV4
from volcengine.base.Request import Request
from volcengine.Credentials import Credentials

from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class RerankClient:
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
        self.ak = ak
        self.sk = sk
        self.host = host
        self.model_name = model_name
        self.model_version = model_version

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

    def rerank_batch(self, query: str, documents: List[str]) -> List[float]:
        """
        Batch rerank documents against a query.

        Args:
            query: Query text
            documents: List of document texts to rank

        Returns:
            List of rerank scores for each document (same order as input)
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
                url=f"http://{self.host}{req.path}",
                headers=req.headers,
                data=req.body,
                timeout=30,
            )

            result = response.json()
            # print(f"[RerankClient] Raw response: {result}")
            if "result" not in result or "data" not in result["result"]:
                logger.warning(f"[RerankClient] Unexpected response format: {result}")
                return [0.0] * len(documents)

            # Each document is a separate group, data array returns scores for each group sequentially
            data = result["result"]["data"]
            scores = [item.get("score", 0.0) for item in data]

            logger.debug(f"[RerankClient] Reranked {len(documents)} documents")
            return scores

        except Exception as e:
            logger.error(f"[RerankClient] Rerank failed: {e}")
            return [0.0] * len(documents)

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

        return cls(
            ak=config.ak,
            sk=config.sk,
            host=config.host,
            model_name=config.model_name,
            model_version=config.model_version,
        )
