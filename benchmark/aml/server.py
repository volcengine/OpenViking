#!/usr/bin/env python3
"""Minimal Agent Memory Leaderboard bridge for OpenViking."""

from __future__ import annotations

import argparse
import asyncio
import hmac
import math
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Awaitable, Callable, Literal, TypeVar

import httpx
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from openviking_sdk import AsyncHTTPClient, OpenVikingError
from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8088
DEFAULT_OPENVIKING_URL = "http://127.0.0.1:1933"
MEMORY_ROOT_URI = "viking://~/memories"
RETRYABLE_OPENVIKING_CODES = {
    "ABORTED",
    "DEADLINE_EXCEEDED",
    "INTERNAL",
    "RESOURCE_EXHAUSTED",
    "UNAVAILABLE",
    "UNKNOWN",
}

T = TypeVar("T")


def _positive_int_env(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} must be at least 1")
    return value


def _positive_float_env(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite number greater than 0")
    return value


@dataclass(frozen=True)
class AMLSettings:
    openviking_url: str = DEFAULT_OPENVIKING_URL
    openviking_api_key: str | None = None
    openviking_account: str | None = None
    aml_api_key: str | None = None
    retry_attempts: int = 3
    retry_delay_seconds: float = 0.5
    request_timeout_seconds: float = 120.0
    extraction_timeout_seconds: float = 900.0
    task_poll_seconds: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "openviking_url", self.openviking_url.rstrip("/"))
        if not self.openviking_url.startswith(("http://", "https://")):
            raise ValueError("OPENVIKING_URL must use http:// or https://")
        if self.openviking_account is not None and not self.openviking_account.strip():
            raise ValueError("OPENVIKING_ACCOUNT must not be blank")
        if self.retry_attempts < 1:
            raise ValueError("retry_attempts must be at least 1")
        for name in (
            "retry_delay_seconds",
            "request_timeout_seconds",
            "extraction_timeout_seconds",
            "task_poll_seconds",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a finite number greater than 0")

    @classmethod
    def from_env(cls) -> "AMLSettings":
        return cls(
            openviking_url=os.getenv("OPENVIKING_URL", DEFAULT_OPENVIKING_URL),
            openviking_api_key=os.getenv("OPENVIKING_API_KEY") or None,
            openviking_account=os.getenv("OPENVIKING_ACCOUNT") or None,
            aml_api_key=os.getenv("AML_API_KEY") or None,
            retry_attempts=_positive_int_env("AML_RETRY_ATTEMPTS", 3),
            retry_delay_seconds=_positive_float_env("AML_RETRY_DELAY_SECONDS", 0.5),
            request_timeout_seconds=_positive_float_env("AML_OV_REQUEST_TIMEOUT_SECONDS", 120.0),
            extraction_timeout_seconds=_positive_float_env(
                "AML_OV_EXTRACTION_TIMEOUT_SECONDS", 900.0
            ),
            task_poll_seconds=_positive_float_env("AML_OV_TASK_POLL_SECONDS", 1.0),
        )


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("value must not be blank")
    return value


class AMLMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)
    timestamp: int | None = Field(default=None, ge=0, strict=True)

    _content_not_blank = field_validator("content")(_not_blank)

    @field_validator("timestamp")
    @classmethod
    def timestamp_is_representable(cls, value: int | None) -> int | None:
        if value is not None:
            try:
                datetime.fromtimestamp(value / 1000, tz=timezone.utc)
            except (OverflowError, OSError, ValueError) as exc:
                raise ValueError(
                    "timestamp is outside the supported Unix millisecond range"
                ) from exc
        return value


class AMLAddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    messages: list[AMLMessage] = Field(min_length=1, max_length=20)
    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)

    _request_id_not_blank = field_validator("request_id")(_not_blank)
    _user_id_not_blank = field_validator("user_id")(_not_blank)
    _session_id_not_blank = field_validator("session_id")(_not_blank)


class AMLSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    options: list[str] | None = None
    user_id: str = Field(min_length=1)
    top_k: int = Field(ge=1, strict=True)

    _query_not_blank = field_validator("query")(_not_blank)
    _user_id_not_blank = field_validator("user_id")(_not_blank)


class OpenVikingBackendError(RuntimeError):
    pass


class OpenVikingBackend:
    def __init__(
        self,
        settings: AMLSettings,
        *,
        client_factory: Callable[[str], AsyncHTTPClient] | None = None,
    ) -> None:
        self.settings = settings
        self._client_factory = client_factory

    def _new_client(self, user_id: str) -> AsyncHTTPClient:
        if self._client_factory is not None:
            return self._client_factory(user_id)
        return AsyncHTTPClient(
            url=self.settings.openviking_url,
            api_key=self.settings.openviking_api_key,
            account=self.settings.openviking_account,
            user=user_id,
            timeout=self.settings.request_timeout_seconds,
            extra_headers={},
            profile_enabled=False,
        )

    @asynccontextmanager
    async def _client(self, user_id: str) -> AsyncIterator[AsyncHTTPClient]:
        client = self._new_client(user_id)
        await client.initialize()
        try:
            yield client
        finally:
            await client.close()

    async def _retry(self, operation: str, call: Callable[[], Awaitable[T]]) -> T:
        for attempt in range(self.settings.retry_attempts):
            try:
                return await call()
            except (httpx.RequestError, OpenVikingError) as exc:
                retryable = isinstance(exc, httpx.RequestError) or (
                    exc.code in RETRYABLE_OPENVIKING_CODES
                )
                if not retryable or attempt + 1 == self.settings.retry_attempts:
                    raise OpenVikingBackendError(f"OpenViking {operation} failed: {exc}") from exc
                await asyncio.sleep(self.settings.retry_delay_seconds * (2**attempt))
        raise AssertionError("retry loop exited unexpectedly")

    async def ready(self) -> bool:
        try:
            async with self._client("aml-health") as client:
                return await client.health()
        except (httpx.RequestError, OpenVikingError, RuntimeError, ValueError):
            return False

    async def add_and_commit(
        self, *, user_id: str, session_id: str, messages: list[dict[str, Any]]
    ) -> None:
        async with self._client(user_id) as client:
            result = await self._retry(
                "batch add",
                lambda: client.batch_add_messages(session_id, messages),
            )
            if not isinstance(result, dict) or result.get("added") != len(messages):
                raise OpenVikingBackendError("OpenViking did not add every message")

            commit = await self._retry(
                "commit",
                lambda: client.commit_session(session_id, keep_recent_count=0),
            )
            if not isinstance(commit, dict):
                raise OpenVikingBackendError("OpenViking did not accept the commit")
            task_id = commit.get("task_id")
            if commit.get("status") != "accepted" or not isinstance(task_id, str) or not task_id:
                raise OpenVikingBackendError("OpenViking did not accept the commit")

            deadline = time.monotonic() + self.settings.extraction_timeout_seconds
            while True:
                task = await self._retry("task poll", lambda: client.get_task(task_id))
                if not isinstance(task, dict):
                    raise OpenVikingBackendError("OpenViking returned an invalid commit task")
                task_status = task.get("status")
                if task_status == "completed":
                    return
                if task_status in {"failed", "cancelled"}:
                    raise OpenVikingBackendError(
                        f"OpenViking commit {task_status}: {task.get('error') or 'unknown error'}"
                    )
                if task_status not in {"pending", "running", "cancelling"}:
                    raise OpenVikingBackendError("OpenViking returned an unknown commit status")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise OpenVikingBackendError("OpenViking commit timed out")
                await asyncio.sleep(min(self.settings.task_poll_seconds, remaining))

    async def find(self, *, user_id: str, query: str, limit: int) -> list[dict[str, Any]]:
        async with self._client(user_id) as client:
            result = await self._retry(
                "find",
                lambda: client.find(
                    query=query,
                    target_uri=MEMORY_ROOT_URI,
                    limit=limit,
                    options={
                        "context_type": ["memory"],
                        "level": 2,
                    },
                ),
            )
        memories = result.get("memories") if isinstance(result, dict) else None
        if not isinstance(memories, list) or any(not isinstance(hit, dict) for hit in memories):
            raise OpenVikingBackendError("OpenViking returned an invalid find result")
        return memories


def _format_timestamp(timestamp_ms: int) -> str:
    return (
        datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class AMLAdapter:
    def __init__(self, settings: AMLSettings, backend: OpenVikingBackend) -> None:
        self.settings = settings
        self.backend = backend

    def authorize(self, request: Request) -> None:
        if self.settings.aml_api_key is None:
            return
        candidates = [request.headers.get("x-api-key", "")]
        scheme, separator, value = request.headers.get("authorization", "").partition(" ")
        if separator and scheme.lower() in {"bearer", "token"}:
            candidates.append(value)
        if any(
            hmac.compare_digest(candidate, self.settings.aml_api_key) for candidate in candidates
        ):
            return
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Memory System Key",
        )

    async def add(self, request: AMLAddRequest) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        for message in request.messages:
            item: dict[str, Any] = {"role": message.role, "content": message.content}
            if message.timestamp is not None:
                item["created_at"] = _format_timestamp(message.timestamp)
            messages.append(item)
        try:
            await self.backend.add_and_commit(
                user_id=request.user_id,
                session_id=request.session_id,
                messages=messages,
            )
        except OpenVikingBackendError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc
        return {
            "success": True,
            "request_id": request.request_id,
            "user_id": request.user_id,
            "session_id": request.session_id,
        }

    async def search(self, request: AMLSearchRequest) -> dict[str, list[dict[str, Any]]]:
        query = request.query
        if request.options:
            options = "\n".join(
                f"{index}. {option}" for index, option in enumerate(request.options, start=1)
            )
            query = f"{query}\n\nAnswer options:\n{options}"
        try:
            hits = await self.backend.find(
                user_id=request.user_id,
                query=query,
                limit=request.top_k,
            )
        except OpenVikingBackendError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc

        data: list[dict[str, Any]] = []
        for hit in hits:
            content = hit.get("content") or hit.get("abstract")
            uri = hit.get("uri")
            if not isinstance(uri, str) or not uri or not isinstance(content, str) or not content:
                continue
            data.append({"id": uri, "content": content})
            if len(data) == request.top_k:
                break
        return {"data": data}


def create_app(
    *,
    settings: AMLSettings | None = None,
    backend: OpenVikingBackend | None = None,
) -> FastAPI:
    resolved_settings = settings or AMLSettings.from_env()
    resolved_backend = backend or OpenVikingBackend(resolved_settings)
    adapter = AMLAdapter(resolved_settings, resolved_backend)
    application = FastAPI(title="OpenViking AML Adapter", version="0.1.0")

    async def require_key(request: Request) -> None:
        adapter.authorize(request)

    @application.get("/health")
    async def health() -> dict[str, str]:
        if not await resolved_backend.ready():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="OpenViking is not ready",
            )
        return {"status": "ok"}

    @application.post("/add", dependencies=[Depends(require_key)])
    async def add(request: AMLAddRequest) -> dict[str, Any]:
        return await adapter.add(request)

    @application.post("/search", dependencies=[Depends(require_key)])
    async def search(request: AMLSearchRequest) -> dict[str, list[dict[str, Any]]]:
        return await adapter.search(request)

    application.state.aml_adapter = adapter
    return application


app = create_app()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the OpenViking AML adapter")
    parser.add_argument("--host", default=os.getenv("AML_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.getenv("AML_PORT", str(DEFAULT_PORT))))
    parser.add_argument("--log-level", default=os.getenv("AML_LOG_LEVEL", "info"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    uvicorn.run(
        create_app(),
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        access_log=False,
    )


if __name__ == "__main__":
    main()
