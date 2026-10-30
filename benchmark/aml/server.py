#!/usr/bin/env python3
"""Minimal Agent Memory Leaderboard bridge for OpenViking."""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import hmac
import math
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Any, AsyncIterator, Awaitable, Callable, Literal, TypeVar

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from openviking_sdk import AsyncHTTPClient, OpenVikingError
from pydantic import BaseModel, ConfigDict, Field, field_validator

from benchmark.aml.audit import (
    audit_event,
    audit_fields,
    configure_audit_logging,
    request_audit,
)
from benchmark.aml.commit_guard import commit_session_with_proof
from benchmark.aml.rerank import RerankError, RerankRetriesExhausted, VikingDBReranker

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8088
DEFAULT_OPENVIKING_URL = "http://127.0.0.1:1933"
MEMORY_ROOT_URI = "viking://~/memories"
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_ADD_IMAGE_BYTES = 30 * 1024 * 1024
MAX_QUERY_IMAGE_BYTES = 30 * 1024 * 1024
MAX_NATIVE_BATCH_MESSAGES = 100
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
    search_candidate_limit: int | None = None
    search_result_limit: int | None = None
    search_score_threshold: float | None = None
    search_rerank_config: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "openviking_url", self.openviking_url.rstrip("/"))
        if not self.openviking_url.startswith(("http://", "https://")):
            raise ValueError("OPENVIKING_URL must use http:// or https://")
        if self.openviking_account is not None and not self.openviking_account.strip():
            raise ValueError("OPENVIKING_ACCOUNT must not be blank")
        if self.retry_attempts < 1:
            raise ValueError("retry_attempts must be at least 1")
        for name in ("search_candidate_limit", "search_result_limit"):
            value = getattr(self, name)
            if value is not None and not 1 <= value <= 1000:
                raise ValueError(f"{name} must be between 1 and 1000")
        if self.search_score_threshold is not None and not math.isfinite(
            self.search_score_threshold
        ):
            raise ValueError("search_score_threshold must be finite")
        if (
            self.search_candidate_limit is not None
            and self.search_result_limit is not None
            and self.search_result_limit > self.search_candidate_limit
        ):
            raise ValueError("search_result_limit must not exceed search_candidate_limit")
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
            search_candidate_limit=(
                _positive_int_env("AML_SEARCH_CANDIDATE_LIMIT", 200)
                if os.getenv("AML_SEARCH_CANDIDATE_LIMIT")
                else None
            ),
            search_result_limit=(
                _positive_int_env("AML_SEARCH_RESULT_LIMIT", 20)
                if os.getenv("AML_SEARCH_RESULT_LIMIT")
                else None
            ),
            search_score_threshold=(
                float(os.environ["AML_SEARCH_SCORE_THRESHOLD"])
                if os.getenv("AML_SEARCH_SCORE_THRESHOLD")
                else None
            ),
            search_rerank_config=os.getenv("AML_SEARCH_RERANK_CONFIG") or None,
        )


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("value must not be blank")
    return value


class AMLTextPart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["text"]
    text: str = Field(min_length=1)

    _text_not_blank = field_validator("text")(_not_blank)


class AMLImageURL(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    detail: Literal["auto", "low", "high"] | None = None

    @field_validator("url")
    @classmethod
    def validate_inline_image(cls, value: str) -> str:
        header, _, encoded = value.partition(",")
        if (
            header
            not in {
                "data:image/jpeg;base64",
                "data:image/png;base64",
                "data:image/webp;base64",
            }
            or not encoded
        ):
            raise ValueError("image_url must be an inline Base64 JPEG, PNG, or WebP Data URI")
        if len(encoded) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
            raise ValueError("image exceeds the 10 MiB decoded size limit")
        try:
            size = len(base64.b64decode(encoded, validate=True))
        except (binascii.Error, ValueError) as exc:
            raise ValueError("image_url contains invalid Base64 data") from exc
        if size > MAX_IMAGE_BYTES:
            raise ValueError("image exceeds the 10 MiB decoded size limit")
        return value

    @property
    def decoded_size(self) -> int:
        # Base64 was validated above; count bytes without decoding images again.
        return len(self.url.partition(",")[2].rstrip("=")) * 3 // 4


class AMLImagePart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["image_url"]
    image_url: AMLImageURL


AMLContentPart = Annotated[AMLTextPart | AMLImagePart, Field(discriminator="type")]


class AMLMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str | list[AMLContentPart] = Field(min_length=1)
    timestamp: int | None = Field(default=None, ge=0, strict=True)

    @field_validator("content")
    @classmethod
    def content_not_blank(cls, value: str | list[AMLContentPart]) -> str | list[AMLContentPart]:
        return _not_blank(value) if isinstance(value, str) else value

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
    messages: list[AMLMessage] = Field(min_length=1)
    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)

    _request_id_not_blank = field_validator("request_id")(_not_blank)
    _user_id_not_blank = field_validator("user_id")(_not_blank)
    _session_id_not_blank = field_validator("session_id")(_not_blank)

    @field_validator("messages")
    @classmethod
    def validate_image_budget(cls, messages: list[AMLMessage]) -> list[AMLMessage]:
        size = sum(
            part.image_url.decoded_size
            for message in messages
            if isinstance(message.content, list)
            for part in message.content
            if isinstance(part, AMLImagePart)
        )
        if size > MAX_ADD_IMAGE_BYTES:
            raise ValueError("images exceed the 30 MiB decoded size limit per Add request")
        return messages


class AMLSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str | list[AMLContentPart] = Field(min_length=1)
    options: list[str] | None = None
    user_id: str = Field(min_length=1)
    top_k: int = Field(ge=1, strict=True)

    _user_id_not_blank = field_validator("user_id")(_not_blank)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str | list[AMLContentPart]) -> str | list[AMLContentPart]:
        if isinstance(value, str):
            return _not_blank(value)
        size = sum(part.image_url.decoded_size for part in value if isinstance(part, AMLImagePart))
        if size > MAX_QUERY_IMAGE_BYTES:
            raise ValueError("images exceed the 30 MiB decoded size limit per Search request")
        return value


class OpenVikingBackendError(RuntimeError):
    pass


def _commit_failure_kind(error: Any) -> str:
    """Classify known task failures without putting task error text in audit logs."""
    if not isinstance(error, str):
        return "commit_failure_unknown"
    if "Conflicting add/update/delete" in error:
        return "memory_operation_conflict"
    if "MemoryUpdateError" in error:
        return "memory_update_error"
    if "failure_kind=empty_response" in error:
        return "extraction_empty_response"
    if "failure_kind=budget_exhausted" in error or "output-token budget" in error:
        return "extraction_output_budget"
    if "failure_kind=parse_error" in error:
        return "extraction_parse_error"
    return "commit_failure_unknown"


class OpenVikingBackend:
    def __init__(
        self,
        settings: AMLSettings,
        *,
        client_factory: Callable[[str], AsyncHTTPClient] | None = None,
    ) -> None:
        self.settings = settings
        self._client_factory = client_factory
        self._user_write_locks: dict[str, asyncio.Lock] = {}

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
                audit_event(
                    "backend_attempt_failed",
                    operation=operation,
                    attempt=attempt + 1,
                    error_type=type(exc).__name__,
                    error_code=getattr(exc, "code", None),
                    will_retry=retryable and attempt + 1 < self.settings.retry_attempts,
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
        # Serialize this user's maintenance across Sessions; independent users
        # still run concurrently. Hold a strong lock reference through polling.
        lock = self._user_write_locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            await self._add_and_commit_locked(
                user_id=user_id, session_id=session_id, messages=messages
            )

    async def _write_once(self, operation: str, call: Callable[[], Awaitable[T]]) -> T:
        try:
            return await call()
        except (httpx.RequestError, OpenVikingError) as exc:
            audit_event(
                "backend_write_failed",
                operation=operation,
                error_type=type(exc).__name__,
                error_code=getattr(exc, "code", None),
            )
            raise OpenVikingBackendError(f"OpenViking {operation} failed: {exc}") from exc

    async def _add_and_commit_locked(
        self, *, user_id: str, session_id: str, messages: list[dict[str, Any]]
    ) -> None:
        async with self._client(user_id) as client:
            for offset in range(0, len(messages), MAX_NATIVE_BATCH_MESSAGES):
                batch = messages[offset : offset + MAX_NATIVE_BATCH_MESSAGES]
                result = await self._write_once(
                    "batch add",
                    lambda batch=batch: client.batch_add_messages(session_id, batch),
                )
                if not isinstance(result, dict) or result.get("added") != len(batch):
                    raise OpenVikingBackendError("OpenViking did not add every message")
            audit_event("messages_added", added_count=len(messages))

            commit = await self._write_once(
                "commit",
                lambda: commit_session_with_proof(client, session_id, audit_event),
            )
            if not isinstance(commit, dict):
                raise OpenVikingBackendError("OpenViking did not accept the commit")
            task_id = commit.get("task_id")
            if commit.get("status") != "accepted" or not isinstance(task_id, str) or not task_id:
                raise OpenVikingBackendError("OpenViking did not accept the commit")
            audit_fields(native_task_id=task_id)
            audit_event("commit_accepted")

            deadline = time.monotonic() + self.settings.extraction_timeout_seconds
            previous_status = None
            while True:
                task = await self._retry("task poll", lambda: client.get_task(task_id))
                if not isinstance(task, dict):
                    raise OpenVikingBackendError("OpenViking returned an invalid commit task")
                task_status = task.get("status")
                if task_status != previous_status:
                    audit_event("commit_progress", task_status=task_status)
                    previous_status = task_status
                if task_status == "completed":
                    task_result = task.get("result") or {}
                    if isinstance(task_result, dict):
                        audit_event("commit_completed", archive_uri=task_result.get("archive_uri"))
                    return
                if task_status in {"failed", "cancelled"}:
                    audit_fields(
                        failure_stage="commit_task",
                        failure_kind=_commit_failure_kind(task.get("error")),
                    )
                    audit_event("commit_failed", task_status=task_status)
                    raise OpenVikingBackendError(
                        f"OpenViking commit {task_status}: {task.get('error') or 'unknown error'}"
                    )
                if task_status not in {"pending", "running", "cancelling"}:
                    raise OpenVikingBackendError("OpenViking returned an unknown commit status")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise OpenVikingBackendError("OpenViking commit timed out")
                await asyncio.sleep(min(self.settings.task_poll_seconds, remaining))

    async def find(
        self, *, user_id: str, query: str, limit: int, image: str | list[str] | None = None
    ) -> list[dict[str, Any]]:
        options: dict[str, Any] = {"context_type": ["memory"], "level": 2}
        if self.settings.search_score_threshold is not None:
            options["score_threshold"] = self.settings.search_score_threshold
        async with self._client(user_id) as client:
            result = await self._retry(
                "find",
                lambda: client.find(
                    query=query,
                    target_uri=MEMORY_ROOT_URI,
                    limit=limit,
                    options=options,
                    **({"image": image} if image else {}),
                ),
            )
        memories = result.get("memories") if isinstance(result, dict) else None
        if not isinstance(memories, list) or any(not isinstance(hit, dict) for hit in memories):
            raise OpenVikingBackendError("OpenViking returned an invalid find result")
        return memories

    async def read_memories(self, *, user_id: str, uris: list[str]) -> list[str]:
        async with self._client(user_id) as client:
            slots = asyncio.Semaphore(8)

            async def read_one(uri: str) -> str:
                async with slots:
                    content = await self._retry("read", lambda: client.read(uri))
                if not isinstance(content, str) or not content.strip():
                    raise OpenVikingBackendError("OpenViking returned an empty memory body")
                return content

            return await asyncio.gather(*(read_one(uri) for uri in uris))


def _format_timestamp(timestamp_ms: int) -> str:
    return (
        datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class AMLAdapter:
    def __init__(
        self,
        settings: AMLSettings,
        backend: OpenVikingBackend,
        reranker: VikingDBReranker | None = None,
    ) -> None:
        self.settings = settings
        self.backend = backend
        self.reranker = reranker or (
            VikingDBReranker(settings.search_rerank_config)
            if settings.search_rerank_config
            else None
        )

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
            item: dict[str, Any] = {"role": message.role}
            if isinstance(message.content, str):
                item["content"] = message.content
            else:
                item["parts"] = [part.model_dump(exclude_none=True) for part in message.content]
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
            audit_event("add_failed", error_type=type(exc).__name__)
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
        images = []
        if isinstance(query, list):
            images = [part.image_url.url for part in query if isinstance(part, AMLImagePart)]
            query = "\n".join(part.text for part in query if isinstance(part, AMLTextPart))
        image_rerank = not images or bool(query.strip())
        if request.options:
            options = "\n".join(
                f"{index}. {option}" for index, option in enumerate(request.options, start=1)
            )
            query = f"{query}\n\nAnswer options:\n{options}"
        try:
            result_limit = min(request.top_k, self.settings.search_result_limit or request.top_k)
            hits = await self.backend.find(
                user_id=request.user_id,
                query=query,
                limit=self.settings.search_candidate_limit or request.top_k,
                **({"image": images[0] if len(images) == 1 else images} if images else {}),
            )
            if self.reranker is not None:
                # URI is the identity; keep the first native hit before reranking.
                candidates = []
                seen: set[str] = set()
                for hit in hits:
                    uri = hit.get("uri")
                    if not isinstance(uri, str) or not uri or uri in seen:
                        continue
                    seen.add(uri)
                    candidates.append(hit)
                if any(
                    not isinstance(hit["uri"], str)
                    or not hit["uri"].startswith(f"viking://user/{request.user_id}/memories/")
                    for hit in candidates
                ):
                    raise OpenVikingBackendError("OpenViking returned an out-of-scope memory")
                ranked = candidates
                search_event = "search_image_ranked"
                fallback_fields: dict[str, Any] = {}
                if image_rerank:
                    try:
                        ranked = await self.reranker.rank(query, candidates)
                        search_event = "search_reranked"
                    except RerankRetriesExhausted as exc:
                        search_event = "search_rerank_fallback"
                        fallback_fields = {
                            "reason": "technical_retries_exhausted",
                            "http_status": exc.http_status,
                            "attempts": exc.attempts,
                        }
                hits = ranked[:result_limit]
                uris = [hit["uri"] for hit in hits]
                bodies = await self.backend.read_memories(user_id=request.user_id, uris=uris)
                hits = [{**hit, "content": body} for hit, body in zip(hits, bodies, strict=True)]
                audit_event(
                    search_event,
                    candidate_count=len(candidates),
                    selected_count=len(hits),
                    **fallback_fields,
                )
        except (OpenVikingBackendError, RerankError) as exc:
            audit_event("search_failed", error_type=type(exc).__name__)
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
            if len(data) == result_limit:
                break
        return {"data": data}


def create_app(
    *,
    settings: AMLSettings | None = None,
    backend: OpenVikingBackend | None = None,
    reranker: VikingDBReranker | None = None,
) -> FastAPI:
    resolved_settings = settings or AMLSettings.from_env()
    resolved_backend = backend or OpenVikingBackend(resolved_settings)
    adapter = AMLAdapter(resolved_settings, resolved_backend, reranker)
    application = FastAPI(title="OpenViking AML Adapter", version="0.1.0")

    @application.middleware("http")
    async def require_key(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Authenticate before routing, including docs, health, redirects and 404s.
        trace_id = uuid.uuid4().hex
        started = time.monotonic()
        known_paths = {
            "/",
            "/health",
            "/add",
            "/search",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/docs/oauth2-redirect",
        }
        # Never log query strings or arbitrary URL paths, which may contain secrets.
        path = request.url.path.rstrip("/") or "/"
        with request_audit(
            secrets=(resolved_settings.aml_api_key, resolved_settings.openviking_api_key),
            trace_id=trace_id,
            method=request.method,
            path=path if path in known_paths else "<unmatched>",
            client_ip=request.client.host if request.client else None,
        ):
            audit_event("request_received")
            response = None
            error_type = None
            try:
                try:
                    adapter.authorize(request)
                except HTTPException as exc:
                    response = JSONResponse(
                        status_code=exc.status_code,
                        content={"detail": exc.detail},
                        headers=exc.headers,
                    )
                else:
                    response = await call_next(request)
                response.headers["X-AML-Trace-Id"] = trace_id
                return response
            except BaseException as exc:
                error_type = type(exc).__name__
                raise
            finally:
                audit_event(
                    "request_handled",
                    http_status=response.status_code if response is not None else None,
                    elapsed_ms=round((time.monotonic() - started) * 1000, 3),
                    error_type=error_type,
                )

    @application.get("/health")
    async def health() -> dict[str, str]:
        if not await resolved_backend.ready():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="OpenViking is not ready",
            )
        return {"status": "ok"}

    @application.post("/add")
    async def add(request: AMLAddRequest) -> dict[str, Any]:
        audit_fields(
            request_id=request.request_id,
            user_id=request.user_id,
            session_id=request.session_id,
            message_count=len(request.messages),
            message_chars=sum(len(message.content) for message in request.messages),
        )
        audit_event("add_started")
        return await adapter.add(request)

    @application.post("/search")
    async def search(request: AMLSearchRequest) -> dict[str, list[dict[str, Any]]]:
        audit_fields(user_id=request.user_id, top_k=request.top_k, query_chars=len(request.query))
        audit_event("search_started")
        result = await adapter.search(request)
        audit_event("search_completed", result_count=len(result["data"]))
        return result

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
    configure_audit_logging()
    uvicorn.run(
        create_app(),
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        access_log=False,
    )


if __name__ == "__main__":
    main()
