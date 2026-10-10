# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OpenViking HTTP adapter. No imports from the server/service implementation."""

import asyncio
from urllib.parse import urlencode

import aiohttp
from packaging.version import InvalidVersion, Version

from .cache import TTLCache


class VikingError(Exception):
    def __init__(self, reason, status=503):
        super().__init__(reason)
        self.reason, self.status = reason, status


class VikingClient:
    def __init__(self, http: aiohttp.ClientSession, base_url: str, min_version: str):
        self.http, self.base_url, self.min_version = http, base_url.rstrip("/"), min_version
        self.unavailable_reason = ""
        # Every caller sees the same MCP tools; the last good list outlives failures.
        self.tools_cache, self.last_tools = TTLCache(ttl=300, capacity=1), None

    async def request(self, method, path, key, body=None, timeout=30):
        try:
            async with self.http.request(
                method,
                self.base_url + path,
                json=body,
                headers={"X-API-Key": key, "Accept-Encoding": "identity"},
                timeout=aiohttp.ClientTimeout(total=timeout),
                allow_redirects=False,
            ) as response:
                if response.status >= 300:
                    raise VikingError(f"openviking_http_{response.status}", response.status)
                data = await response.json()
                if data.get("status") == "error":
                    raise VikingError("openviking_" + data.get("error", {}).get("code", "error"))
                return data.get("result", data)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
            raise VikingError("openviking_unavailable") from error

    async def health(self, key="", require_identity=True):
        result = await self.request("GET", "/health", key, timeout=5)
        try:
            if Version(result.get("version", "0")) < Version(self.min_version):
                raise VikingError("openviking_version_mismatch")
        except InvalidVersion as error:
            raise VikingError("openviking_version_mismatch") from error
        if require_identity:
            if result.get("role", "").lower() == "root":
                raise VikingError("root_key_not_allowed", 403)
            if not result.get("user_id") or not result.get("account_id"):
                raise VikingError("openviking_identity_missing", 401)
        return result

    async def recall(self, key, query, policy, exclude, budget):
        if self.unavailable_reason:
            raise VikingError(self.unavailable_reason)
        return await self.request(
            "POST",
            "/api/v1/search/search",
            key,
            {
                "query": query,
                "mode": "context",
                "query_expansion": "off",
                "context_type": policy.context_types,
                "max_tokens": budget,
                "quotas": policy.quotas or None,
                "score_threshold": policy.score_threshold,
                "exclude_uris": exclude[-200:],
                "rewrite": False,
            },
            timeout=policy.recall_timeout,
        )

    async def write(self, key, session, messages):
        return await self.request(
            "POST", f"/api/v1/sessions/{session}/messages/batch", key, {"messages": messages}
        )

    async def create_session(self, key, session):
        try:
            return await self.request(
                "POST",
                "/api/v1/sessions",
                key,
                {
                    "session_id": session,
                    "auto_commit_policy": {
                        "pending_token_threshold": 0,
                        "message_count_threshold": 0,
                        "idle_timeout_seconds": 0,
                    },
                    # Commits only feed memory extraction; the gateway writes its own summaries.
                    "memory_policy": {"working_memory": {"enabled": False}},
                },
            )
        except VikingError as error:
            if error.status != 409:
                raise

    async def commit(self, key, session, keep=0):
        return await self.request(
            "POST",
            f"/api/v1/sessions/{session}/commit",
            key,
            {"keep_recent_count": keep},
            timeout=45,
        )

    async def read_content(self, key, uri):
        try:
            return await self.request(
                "GET", "/api/v1/content/read?" + urlencode({"uri": uri, "raw": "true"}), key
            )
        except VikingError as error:
            if error.status == 404 or error.reason == "openviking_NOT_FOUND":
                return None
            raise

    async def pending_tokens(self, key, session):
        info = await self.request("GET", f"/api/v1/sessions/{session}", key)
        return info.get("pending_tokens", 0)

    async def archive_state(self, key, uri):
        for marker, state in ((".done", "completed"), (".failed.json", "failed")):
            if await self.read_content(key, uri.rstrip("/") + "/" + marker) is not None:
                return state
        return "pending"

    async def tools(self, key):
        async def load():
            try:
                result = await self.mcp("tools/list", key, timeout=5)
                tools = result.get("tools") if isinstance(result, dict) else None
                if not isinstance(tools, list) or not all(
                    isinstance(tool, dict)
                    and isinstance(tool.get("name"), str)
                    and isinstance(tool.get("inputSchema"), dict)
                    for tool in tools
                ):
                    raise VikingError("openviking_mcp_invalid_catalog")
                self.last_tools = tools
            except VikingError:
                if self.last_tools is None:
                    raise
            return self.last_tools

        return await self.tools_cache.get("tools/list", load)

    async def mcp(self, method, key, params=None, timeout=120):
        """Stateless Streamable HTTP MCP; accept both JSON and SSE responses."""
        from .protocols import SSEDecoder

        try:
            async with self.http.post(
                self.base_url + "/mcp",
                headers={
                    "X-API-Key": key,
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": "2025-06-18",
                    "Accept-Encoding": "identity",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": method,
                    "params": params or {},
                },
                timeout=aiohttp.ClientTimeout(total=timeout),
                allow_redirects=False,
            ) as response:
                if response.status >= 300:
                    raise VikingError(f"openviking_http_{response.status}", response.status)
                if "text/event-stream" in response.headers.get("content-type", ""):
                    decoder = SSEDecoder()
                    async for chunk in response.content.iter_any():
                        for frame in decoder.feed(chunk):
                            value = decoder.data(frame)
                            if value and value.get("id") == 1:
                                if "error" in value:
                                    raise VikingError("openviking_mcp_error")
                                return value["result"]
                    raise VikingError("openviking_mcp_incomplete")
                value = await response.json()
                if "error" in value:
                    raise VikingError("openviking_mcp_error")
                return value["result"]
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError) as error:
            raise VikingError("openviking_unavailable") from error

    async def upload(self, token, filename, content):
        data = aiohttp.FormData()
        data.add_field("file", content, filename=filename, content_type="application/octet-stream")
        try:
            async with self.http.post(
                self.base_url + "/api/v1/resources/temp_upload",
                params={"token": token},
                headers={"Accept-Encoding": "identity"},
                data=data,
                timeout=aiohttp.ClientTimeout(total=120),
                allow_redirects=False,
            ) as response:
                if response.status >= 300:
                    raise VikingError("openviking_upload_failed", response.status)
                return await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
            raise VikingError("openviking_upload_unavailable") from error
