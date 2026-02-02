# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import json
import os
import time
from typing import Any, Dict, List, Optional

import requests
from volcengine.auth.SignerV4 import SignerV4
from volcengine.base.Request import Request
from volcengine.Credentials import Credentials

from openviking.storage.vectordb.vectorize.base import BaseVectorizer, VectorizeResult


class ClientForDataApi:
    def __init__(self, ak, sk, host, region):
        self.ak = ak
        self.sk = sk
        self.host = host
        self.region = region

    def prepare_request(self, method, path, params=None, data=None):
        r = Request()
        r.set_shema("https")
        r.set_method(method)
        r.set_connection_timeout(10)
        r.set_socket_timeout(10)
        mheaders = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Host": self.host,
        }
        r.set_headers(mheaders)
        if params:
            r.set_query(params)
        r.set_host(self.host)
        r.set_path(path)
        if data is not None:
            r.set_body(json.dumps(data))
        credentials = Credentials(self.ak, self.sk, "vikingdb", self.region)
        SignerV4.sign(r, credentials)
        return r

    def do_req(self, req_method, req_path, req_params, req_body):
        req = self.prepare_request(
            method=req_method, path=req_path, params=req_params, data=req_body
        )
        return requests.request(
            method=req.method,
            url=f"https://{self.host}{req.path}",
            headers=req.headers,
            data=req.body,
            timeout=10000,
        )


class VolcengineVectorizer(BaseVectorizer):
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize Volcengine vectorizer

        Args:
            config: Configuration dictionary
                - AK: Access Key (required)
                - SK: Secret Key (required)
                - Host: API domain (required)
                - APIPath: API path (default /api/vikingdb/embedding)
                - DenseModelName: Dense model name
                - DenseModelVersion: Dense model version
                - SparseModelName: Sparse model name
                - SparseModelVersion: Sparse model version
                - Dim: Dense vector dimension
                - RetryTimes: Retry count
                - RetryDelay: Base retry delay
        """
        # Merge default config and user config
        self.full_config = config
        super().__init__(self.full_config)

        self.ak = self.full_config.get("AK", os.environ.get("VOLC_AK"))
        self.sk = self.full_config.get("SK", os.environ.get("VOLC_SK"))
        self.host = self.full_config.get("Host", os.environ.get("VOLC_HOST"))
        self.region = self.full_config.get("Region", os.environ.get("VOLC_REGION"))

        if not self.ak or not self.sk or not self.host or not self.region:
            raise ValueError("AK, SK, Host, Region must set")

        # Initialize AK/SK signature client
        self.api_client = ClientForDataApi(
            ak=self.ak, sk=self.sk, host=self.host, region=self.region
        )

        # Extract core configuration
        self.api_path = self.full_config.get("APIPath", "/api/vikingdb/embedding")
        self.retry_times = self.full_config.get("RetryTimes", 3)
        self.retry_delay = self.full_config.get("RetryDelay", 1)
        self.dim = self.full_config.get("Dim", 0)

    def vectorize_query(self, texts: List[str]) -> VectorizeResult:
        """
        Vectorize query text

        Args:
            texts: Text list

        Returns:
            VectorizeResult: Vectorization result
        """
        dense_model = {
            "name": self.full_config.get("DenseModelName", ""),
            "version": self.full_config.get("DenseModelVersion", "default"),
        }
        sparse_model = None
        if self.full_config.get("SparseModelName"):
            sparse_model = {
                "name": self.full_config.get("SparseModelName", ""),
                "version": self.full_config.get("SparseModelVersion", "default"),
            }

        data = [{"text": t} for t in texts]
        return self.vectorize_document(data, dense_model, sparse_model)

    def _build_request_body(
        self, data: List[Any], dense_model: Dict[str, Any], sparse_model: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """Build embedding request body"""
        if not isinstance(data, list) or not all(isinstance(t, dict) for t in data):
            raise ValueError("data must be a list of dictionaries")

        req: Dict[str, Any] = {
            "dense_model": {
                "name": dense_model["name"],
                "version": dense_model.get("version", "default"),
            },
            "data": data,
        }
        if "dim" in dense_model:
            req["dense_model"]["dim"] = dense_model["dim"]

        if sparse_model:
            req["sparse_model"] = {
                "name": sparse_model["name"],
                "version": sparse_model.get("version", "default"),
            }
        return req

    def _parse_response(
        self,
        response_dict: Dict[str, Any],
        dense_model: Dict[str, Any],
        sparse_model: Dict[str, Any] = None,
    ) -> VectorizeResult:
        """Parse API response"""
        # Basic validation
        if not isinstance(response_dict, dict):
            raise ValueError("response must be dictionary type")

        # Check call status
        if response_dict.get("code") != "Success":
            raise RuntimeError(
                f"API call failed: {response_dict.get('message', 'unknown error')}, "
                f"request_id: {response_dict.get('request_id', 'none')}"
            )

        # Wrap result
        result = VectorizeResult()
        result.request_id = response_dict.get("request_id", "")

        # Parse embedding data
        embedding_data = response_dict["result"]["data"]
        result.dense_vectors = [item["dense"] for item in embedding_data]
        result.sparse_vectors = [item["sparse"] for item in embedding_data] if sparse_model else []

        # Parse token usage information
        result.token_usage = response_dict["result"]["token_usage"]
        return result

    def vectorize_document(
        self,
        data: List[Any],
        dense_model: Dict[str, Any],
        sparse_model: Optional[Dict[str, Any]] = None,
    ) -> VectorizeResult:
        """
        Text vectorization core method

        Args:
            data: Data list to vectorize

        Returns:
            VectorizeResult: Vectorization result

        Raises:
            ValueError: Input parameter error
            RuntimeError: Request failed or response parsing failed
        """
        if not data:
            raise ValueError("data list cannot be empty")

        # Build request body
        req_body = self._build_request_body(data, dense_model, sparse_model)

        # Request logic with retry
        retry_count = 0
        last_exception = None

        while retry_count <= self.retry_times:
            try:
                # Send request using AK/SK signature client
                resp = self.api_client.do_req(
                    req_method="POST",
                    req_path=self.api_path,
                    req_params=None,
                    req_body=req_body,
                )

                # Check HTTP status code
                resp.raise_for_status()

                # Parse JSON response
                resp_dict = resp.json()

                # Parse and return result
                return self._parse_response(resp_dict, dense_model, sparse_model)

            except (
                requests.exceptions.RequestException,
                json.JSONDecodeError,
                RuntimeError,
            ) as e:
                last_exception = e
                retry_count += 1

                # Raise exception if retry limit exceeded
                if retry_count > self.retry_times:
                    raise RuntimeError(
                        f"request failed (after {self.retry_times} retries): {str(e)}"
                    ) from e

                # Exponential backoff delay
                delay = self.retry_delay * (2 ** (retry_count - 1))
                time.sleep(delay)

        # Fallback exception (should not reach here)
        raise RuntimeError(f"request exception: {str(last_exception)}")

    def get_dense_vector_dim(
        self, dense_model: Dict[str, Any], sparse_model: Optional[Dict[str, Any]] = None
    ) -> int:
        """Get dense vector dimension"""
        if self.dim > 0:
            return self.dim
        test_data = [{"text": "volcengine vectorizer health check"}]
        try:
            result = self.vectorize_document(test_data, dense_model, sparse_model)
            # Validate result validity
            return len(result.dense_vectors[0]) if result.dense_vectors else 0
        except Exception:
            return 0

    def close(self):
        """Close resources (interface compatibility)"""
        pass

    def __del__(self):
        """Destructor"""
        self.close()
