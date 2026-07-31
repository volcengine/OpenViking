# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Thin async HTTP client around the OpenViking server."""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from .config import Settings


class OVError(Exception):
    """Raised when the OpenViking server returns a non-2xx response."""

    def __init__(self, status: int, payload: Any):
        self.status = status
        self.payload = payload
        super().__init__(f"OpenViking error {status}: {payload!r}")


class OVClient:
    """Forward HTTP calls to OpenViking, attaching tenant headers."""

    def __init__(self, settings: Settings, client: Optional[httpx.AsyncClient] = None):
        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=settings.endpoint,
            timeout=settings.timeout_seconds,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if self._settings.api_key:
            headers["Authorization"] = f"Bearer {self._settings.api_key}"
        if self._settings.account:
            headers["X-OpenViking-Account"] = self._settings.account
        if self._settings.user:
            headers["X-OpenViking-User"] = self._settings.user
        if self._settings.agent:
            headers["X-OpenViking-Actor-Peer"] = self._settings.agent
        return headers

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> Any:
        response = await self._client.request(
            method,
            path,
            params=params,
            json=json,
            headers=self._headers(),
        )
        try:
            payload = response.json()
        except ValueError:
            payload = response.text
        if response.status_code >= 400:
            raise OVError(response.status_code, payload)
        return payload

    async def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        return await self.request("GET", path, params=params)

    async def post(self, path: str, json: Optional[Dict[str, Any]] = None) -> Any:
        return await self.request("POST", path, json=json)
