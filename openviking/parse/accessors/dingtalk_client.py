# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Small read-only client for the DingTalk Streamable HTTP MCP services."""

import asyncio
import contextvars
import json
import logging
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, AsyncIterator

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from openviking_cli.utils.config.dingtalk_config import DingTalkIdentityConfig

_TOOLS = {
    "doc": frozenset(
        {
            "get_document_info",
            "list_nodes",
            "get_document_content",
            "list_document_blocks",
            "download_doc_attachment",
            "download_file",
        }
    ),
    "sheets": frozenset(
        {
            "get_all_sheets",
            "get_sheet",
            "get_range_as_csv",
        }
    ),
    "ai_table": frozenset(
        {
            "get_base",
            "get_tables",
            "get_fields",
            "query_records",
        }
    ),
}
_PERMISSION_MARKERS = (
    "permission",
    "forbidden",
    "unauthorized",
    "access denied",
    "no privilege",
    "无权限",
    "未授权",
)
_sensitive_logs = contextvars.ContextVar("dingtalk_sensitive_logs", default=False)


class _SensitiveLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _sensitive_logs.get()


_filter = _SensitiveLogFilter()
for _logger_name in (
    "httpx",
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
    "mcp.client.streamable_http",
):
    logging.getLogger(_logger_name).addFilter(_filter)


class DingTalkMCPError(RuntimeError):
    """Credential-safe DingTalk MCP failure."""

    def __init__(
        self,
        operation: str,
        *,
        permission_denied: bool = False,
        reason_code: str | None = None,
        retryable: bool = False,
    ):
        self.operation = operation
        self.permission_denied = permission_denied
        self.reason_code = reason_code
        self.retryable = retryable
        detail = "permission denied" if permission_denied else "request failed"
        super().__init__(f"DingTalk {operation} {detail}")


def _result_payload(result: Any) -> dict[str, Any]:
    if isinstance(getattr(result, "structuredContent", None), dict):
        return dict(result.structuredContent)
    for item in getattr(result, "content", ()):
        text = getattr(item, "text", None)
        if not isinstance(text, str):
            continue
        try:
            parsed = json.loads(text)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            return parsed
        return {"items": parsed}
    return {}


def _looks_like_permission_error(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False, default=str).lower()
    return any(marker in text for marker in _PERMISSION_MARKERS)


def _permission_exception(exc: BaseException) -> bool:
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status in {401, 403}:
        return True
    nested = getattr(exc, "exceptions", ())
    return any(_permission_exception(child) for child in nested)


class DingTalkClient:
    """Calls only the explicitly allowed read operations."""

    def __init__(
        self,
        identity: DingTalkIdentityConfig,
        *,
        timeout_seconds: float = 30.0,
        max_attempts: int = 3,
    ) -> None:
        self.identity = identity
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts

    async def call(self, service: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool not in _TOOLS.get(service, ()):
            raise ValueError(f"DingTalk MCP tool is not allowed: {service}.{tool}")
        operation = f"{service}.{tool}"
        for attempt in range(self.max_attempts):
            try:
                return await self._call_once(service, tool, arguments)
            except DingTalkMCPError as exc:
                if exc.permission_denied or not exc.retryable or attempt + 1 == self.max_attempts:
                    raise
            except (httpx.TimeoutException, httpx.TransportError, TimeoutError, OSError):
                if attempt + 1 == self.max_attempts:
                    raise DingTalkMCPError(operation) from None
            except Exception as exc:
                if _permission_exception(exc):
                    raise DingTalkMCPError(operation, permission_denied=True) from None
                if attempt + 1 == self.max_attempts:
                    raise DingTalkMCPError(operation) from None
            await asyncio.sleep(0.2 * (2**attempt))
        raise DingTalkMCPError(operation)

    async def _call_once(
        self,
        service: str,
        tool: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        endpoint = getattr(self.identity, service)
        operation = f"{service}.{tool}"
        if endpoint is None:
            raise DingTalkMCPError(operation)
        token = _sensitive_logs.set(True)
        try:
            async with httpx.AsyncClient(
                headers=endpoint.headers,
                timeout=self.timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            ) as http_client:
                async with streamable_http_client(endpoint.url, http_client=http_client) as streams:
                    read_stream, write_stream, _ = streams
                    async with ClientSession(
                        read_stream,
                        write_stream,
                        read_timeout_seconds=timedelta(seconds=self.timeout_seconds),
                    ) as session:
                        await session.initialize()
                        result = await session.call_tool(tool, arguments)
        finally:
            _sensitive_logs.reset(token)

        payload = _result_payload(result)
        if getattr(result, "isError", False) or payload.get("success") is False:
            serialized = json.dumps(payload, ensure_ascii=False, default=str).upper()
            reason_code = str(payload.get("errorCode") or "").strip() or next(
                (
                    code
                    for code in (
                        "INVALID_CURSOR",
                        "CURSOR_SNAPSHOT_CHANGED",
                        "CURSOR_SNAPSHOT_UNAVAILABLE",
                        "NON_RESUMABLE_ERROR_CURSOR",
                        "CURSOR_OFFSET_LIMIT",
                    )
                    if code in serialized
                ),
                None,
            )
            retryable = any(
                marker in serialized
                for marker in (
                    "RATE_LIMIT",
                    "TOO_MANY_REQUESTS",
                    "TIMEOUT",
                    "UNAVAILABLE",
                    "INTERNALERROR",
                    "INTERNAL SERVER ERROR",
                )
            )
            raise DingTalkMCPError(
                operation,
                permission_denied=_looks_like_permission_error(payload),
                reason_code=reason_code,
                retryable=retryable,
            )
        return payload


@asynccontextmanager
async def sensitive_download_logs() -> AsyncIterator[None]:
    """Suppress third-party request logs while a signed URL is in scope."""
    token = _sensitive_logs.set(True)
    try:
        yield
    finally:
        _sensitive_logs.reset(token)
