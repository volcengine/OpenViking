# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Lightweight Qdrant REST client helpers used by the adapter."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

import httpx
import requests


class QdrantRestError(RuntimeError):
    """Raised when a Qdrant REST request fails."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_text: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


class QdrantRestClient:
    """Minimal REST client for Qdrant.

    The implementation intentionally uses REST instead of `qdrant-client` so the
    adapter can be developed and unit-tested without introducing a hard runtime
    dependency on the Python SDK.

    This client is intentionally synchronous because the current ICollection
    contract is synchronous across backends. Async OpenViking code paths must
    either:

    1. use the async backend façade (`_AsyncVectorAdapter`) that wraps adapter
       calls with `asyncio.to_thread`, or
    2. use `request_async()` / `collection_exists_async()` directly.
    """

    def __init__(
        self,
        *,
        url: str,
        api_key: str | None = None,
        timeout_seconds: int = 10,
    ) -> None:
        normalized = url.strip()
        if normalized and not normalized.startswith(("http://", "https://")):
            normalized = f"http://{normalized}"
        self._base_url = normalized.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._session = requests.Session()
        self._headers: Dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            self._headers["api-key"] = api_key

    @property
    def base_url(self) -> str:
        return self._base_url

    def close(self) -> None:
        self._session.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        expected_statuses: tuple[int, ...] = (200,),
    ) -> Dict[str, Any]:
        response = self._session.request(
            method=method.upper(),
            url=f"{self._base_url}{path}",
            headers=self._headers,
            json=json_body,
            params=params,
            timeout=self._timeout_seconds,
        )
        if response.status_code not in expected_statuses:
            raise QdrantRestError(
                f"Qdrant request failed: {method.upper()} {path} -> {response.status_code}",
                status_code=response.status_code,
                response_text=response.text,
            )

        if not response.text:
            return {}

        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise QdrantRestError(
                f"Qdrant returned non-JSON response for {method.upper()} {path}",
                status_code=response.status_code,
                response_text=response.text,
            ) from exc

        if isinstance(data, dict) and data.get("status") == "error":
            raise QdrantRestError(
                f"Qdrant reported error for {method.upper()} {path}: {data.get('result') or data}",
                status_code=response.status_code,
                response_text=response.text,
            )
        return data if isinstance(data, dict) else {"result": data}

    async def request_async(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        expected_statuses: tuple[int, ...] = (200,),
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(
            self.request,
            method,
            path,
            json_body=json_body,
            params=params,
            expected_statuses=expected_statuses,
        )

    def collection_exists(self, collection_name: str) -> bool:
        response = self._session.get(
            f"{self._base_url}/collections/{collection_name}",
            headers=self._headers,
            timeout=self._timeout_seconds,
        )
        if response.status_code == 404:
            return False
        if response.status_code != 200:
            raise QdrantRestError(
                f"Failed to check Qdrant collection existence: {collection_name}",
                status_code=response.status_code,
                response_text=response.text,
            )
        return True

    async def collection_exists_async(self, collection_name: str) -> bool:
        return await asyncio.to_thread(self.collection_exists, collection_name)


class AsyncQdrantRestClient:
    """Async REST client for Qdrant based on httpx.AsyncClient.

    This is provided for async-only call sites that should not rely on
    `asyncio.to_thread`. The current Qdrant ICollection implementation remains
    synchronous because it follows the shared ICollection interface.
    """

    def __init__(
        self,
        *,
        url: str,
        api_key: str | None = None,
        timeout_seconds: int = 10,
    ) -> None:
        normalized = url.strip()
        if normalized and not normalized.startswith(("http://", "https://")):
            normalized = f"http://{normalized}"
        self._base_url = normalized.rstrip("/")
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["api-key"] = api_key
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=timeout_seconds,
        )

    @property
    def base_url(self) -> str:
        return self._base_url

    async def close(self) -> None:
        await self._client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        expected_statuses: tuple[int, ...] = (200,),
    ) -> Dict[str, Any]:
        response = await self._client.request(
            method=method.upper(),
            url=path,
            json=json_body,
            params=params,
        )
        if response.status_code not in expected_statuses:
            raise QdrantRestError(
                f"Qdrant request failed: {method.upper()} {path} -> {response.status_code}",
                status_code=response.status_code,
                response_text=response.text,
            )

        if not response.text:
            return {}

        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise QdrantRestError(
                f"Qdrant returned non-JSON response for {method.upper()} {path}",
                status_code=response.status_code,
                response_text=response.text,
            ) from exc

        if isinstance(data, dict) and data.get("status") == "error":
            raise QdrantRestError(
                f"Qdrant reported error for {method.upper()} {path}: {data.get('result') or data}",
                status_code=response.status_code,
                response_text=response.text,
            )
        return data if isinstance(data, dict) else {"result": data}

    async def collection_exists(self, collection_name: str) -> bool:
        response = await self._client.get(f"/collections/{collection_name}")
        if response.status_code == 404:
            return False
        if response.status_code != 200:
            raise QdrantRestError(
                f"Failed to check Qdrant collection existence: {collection_name}",
                status_code=response.status_code,
                response_text=response.text,
            )
        return True
