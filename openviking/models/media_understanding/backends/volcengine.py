# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""VolcEngine Ark Files + Responses media understanding client."""

import asyncio
import logging
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import volcenginesdkarkruntime

from openviking.models.media_understanding.base import MediaType, MediaUnderstandingClient
from openviking.utils.async_client_cache import LoopScopedAsyncClientCache
from openviking.utils.model_retry import retry_async

logger = logging.getLogger(__name__)

_RETRYABLE_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})
_PERMANENT_429_MARKERS = (
    "accountoverdue",
    "overdue",
    "quota",
    "insufficient",
    "balance",
    "billing",
    "payment",
    "authentication",
    "unauthorized",
    "forbidden",
    "permission",
    "accessdenied",
    "contentpolicy",
    "content_filter",
    "moderation",
    "safety",
    "unsupported",
    "invalid",
    "badrequest",
    "modelnot",
    "notfound",
)


class ArkFileProcessingFailedError(RuntimeError):
    """Terminal failure reported explicitly by Ark Files processing."""


def _load_native_transient_error_types() -> tuple[type[BaseException], ...]:
    error_types: list[type[BaseException]] = []
    try:
        from volcenginesdkarkruntime._exceptions import (
            ArkAPIConnectionError,
            ArkAPITimeoutError,
        )

        error_types.extend((ArkAPIConnectionError, ArkAPITimeoutError))
    except (ImportError, AttributeError):
        pass

    try:
        import httpx

        error_types.extend((httpx.TimeoutException, httpx.ConnectError))
    except (ImportError, AttributeError):
        pass
    return tuple(error_types)


_NATIVE_TRANSIENT_ERROR_TYPES = _load_native_transient_error_types()


def _exception_chain(error: BaseException):
    pending = [error]
    seen = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        cause = getattr(current, "__cause__", None)
        context = getattr(current, "__context__", None)
        if cause is not None:
            pending.append(cause)
        if context is not None:
            pending.append(context)


def _structured_status_code(error: BaseException) -> int | None:
    try:
        value = getattr(error, "status_code", None)
        if value is None or isinstance(value, bool):
            return None
        return int(value)
    except BaseException:
        return None


def _safe_attribute(error: BaseException, name: str) -> Any:
    try:
        return getattr(error, name, None)
    except BaseException:
        return None


def _structured_error_text(error: BaseException) -> str:
    values: list[str] = []
    for name in ("code", "type", "message"):
        value = _safe_attribute(error, name)
        if isinstance(value, str):
            values.append(value)
    body = _safe_attribute(error, "body")
    if isinstance(body, Mapping):
        nested = body.get("error")
        mappings = (body, nested) if isinstance(nested, Mapping) else (body,)
        for item in mappings:
            for name in ("code", "type", "message"):
                value = item.get(name)
                if isinstance(value, str):
                    values.append(value)
    return " ".join(values).lower().replace(" ", "")


def _is_permanent_structured_429(error: BaseException) -> bool:
    semantic_text = _structured_error_text(error)
    return any(marker in semantic_text for marker in _PERMANENT_429_MARKERS)


def _first_structured_value(error: BaseException, name: str) -> str:
    for item in _exception_chain(error):
        value = _safe_attribute(item, name)
        if value is not None and not isinstance(value, (dict, list, tuple, set)):
            try:
                return str(value)[:128]
            except BaseException:
                continue
        body = _safe_attribute(item, "body")
        if isinstance(body, Mapping):
            nested = body.get("error")
            for mapping in (body, nested):
                if isinstance(mapping, Mapping):
                    value = mapping.get(name)
                    if value is not None and not isinstance(value, (dict, list, tuple, set)):
                        try:
                            return str(value)[:128]
                        except BaseException:
                            continue
    return "-"


def _log_sanitized_exception(stage: str, error: BaseException, level: int) -> None:
    status = next(
        (
            status
            for item in _exception_chain(error)
            if (status := _structured_status_code(item)) is not None
        ),
        None,
    )
    logger.log(
        level,
        "Ark media operation exception: stage=%s error_type=%s status=%s code=%s request_id=%s",
        stage,
        type(error).__name__,
        status if status is not None else "-",
        _first_structured_value(error, "code"),
        _first_structured_value(error, "request_id"),
    )


def _is_retryable_media_error(error: Exception) -> bool:
    try:
        return _classify_retryable_media_error(error)
    except BaseException:
        return False


def _classify_retryable_media_error(error: Exception) -> bool:
    chain = tuple(_exception_chain(error))

    if any(isinstance(item, ArkFileProcessingFailedError) for item in chain):
        return False

    structured_statuses = []
    has_structured_429 = False
    for item in chain:
        status_code = _structured_status_code(item)
        if status_code is None:
            continue
        structured_statuses.append((item, status_code))
        if status_code not in _RETRYABLE_HTTP_STATUSES:
            return False
        has_structured_429 = has_structured_429 or status_code == 429

    if has_structured_429 and any(
        _is_permanent_structured_429(item) for item in chain
    ):
        return False

    if structured_statuses:
        return True

    for item in chain:
        if isinstance(
            item,
            (TimeoutError, ConnectionError, *_NATIVE_TRANSIENT_ERROR_TYPES),
        ):
            return True
    return False


class VolcengineMediaUnderstandingClient(MediaUnderstandingClient):
    """Understand audio and video through VolcEngine Ark."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        super().__init__(int(config.get("max_concurrent", 4)))
        self.api_key = str(config["api_key"])
        self.model = str(config["model"])
        self.api_base = str(
            config.get("api_base", "https://ark.cn-beijing.volces.com/api/v3")
        )
        self.timeout = float(config.get("timeout", 600.0))
        self.file_processing_timeout = float(config.get("file_processing_timeout", 1800.0))
        self.file_poll_interval = float(config.get("file_poll_interval", 3.0))
        self.max_retries = int(config.get("max_retries", 3))
        self.max_output_tokens = int(config.get("max_output_tokens", 4096))
        self.fps = float(config.get("fps", 1.0))
        self.extra_headers = dict(config.get("extra_headers") or {})
        self._cleanup_timeout = 5.0
        self._remote_file_ttl_seconds = max(
            3600,
            int(self.file_processing_timeout + self.timeout + 300),
        )
        self._client_cache = LoopScopedAsyncClientCache()

    def _build_async_client(self):
        return volcenginesdkarkruntime.AsyncArk(
            api_key=self.api_key,
            base_url=self.api_base,
            timeout=self.timeout,
            max_retries=0,
        )

    async def _understand(
        self,
        *,
        content: bytes,
        filename: str,
        media_type: MediaType,
        prompt: str,
    ) -> str:
        if len(content) > 512 * 1024 * 1024:
            raise ValueError("Ark media file exceeds the 512 MB limit")
        suffix = Path(filename).suffix.lower()
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
                temp_path = Path(temp_file.name)
                temp_file.write(content)
            return await self._understand_path(
                path=temp_path,
                filename=filename,
                media_type=media_type,
                prompt=prompt,
            )
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    _log_sanitized_exception(
                        "local_cleanup", cleanup_error, logging.WARNING
                    )

    async def _understand_path(
        self,
        *,
        path: Path,
        filename: str,
        media_type: MediaType,
        prompt: str,
    ) -> str:
        if path.stat().st_size > 512 * 1024 * 1024:
            raise ValueError("Ark media file exceeds the 512 MB limit")

        async def attempt_with_sanitized_logging() -> str:
            try:
                return await self._attempt(
                    path=path,
                    filename=filename,
                    media_type=media_type,
                    prompt=prompt,
                )
            except Exception as error:
                _log_sanitized_exception("request", error, logging.WARNING)
                raise

        return await retry_async(
            attempt_with_sanitized_logging,
            max_retries=self.max_retries,
            is_retryable=_is_retryable_media_error,
            logger=None,
        )

    async def _attempt(
        self,
        *,
        path: Path,
        filename: str,
        media_type: MediaType,
        prompt: str,
    ) -> str:
        file_id = None
        try:
            preprocess = {"video": {"fps": self.fps}} if media_type == "video" else None
            client = self._client_cache.get(self._build_async_client)
            with path.open("rb") as upload_file:
                uploaded = await client.files.create(
                    file=upload_file,
                    purpose="user_data",
                    expire_at=int(time.time()) + self._remote_file_ttl_seconds,
                    preprocess_configs=preprocess,
                    extra_headers=self.extra_headers,
                )
            file_id = uploaded.id
            processed = await client.files.wait_for_processing(
                file_id,
                poll_interval=self.file_poll_interval,
                max_wait_seconds=self.file_processing_timeout,
            )
            if processed.status != "active":
                if processed.status == "failed":
                    processing_error = getattr(processed, "error", None)
                    message = (
                        getattr(processing_error, "message", None)
                        or "Ark file processing failed"
                    )
                    raise ArkFileProcessingFailedError(message)
                raise RuntimeError("Ark file processing did not become active")

            response_started = time.monotonic()
            response = await client.responses.create(
                model=self.model,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": (
                                    "input_audio" if media_type == "audio" else "input_video"
                                ),
                                "file_id": file_id,
                            },
                            {"type": "input_text", "text": prompt},
                        ],
                    }
                ],
                max_output_tokens=self.max_output_tokens,
                store=False,
                extra_headers=self.extra_headers,
            )
            if getattr(response, "status", None) != "completed":
                raise RuntimeError("Ark media response did not complete")
            response_id = str(getattr(response, "id", "") or "")
            if response_id:
                logger.debug("Ark media response completed: response_id=%s", response_id)
            text = self._extract_response_text(response)
            if not text:
                raise RuntimeError("Ark media response contained no output text")
            self._record_usage(response, time.monotonic() - response_started)
            return text
        finally:
            if file_id:
                await self._delete_remote_file(file_id)

    async def _delete_remote_file(self, file_id: str) -> None:
        client = self._client_cache.get(self._build_async_client)

        async def bounded_delete() -> None:
            await asyncio.wait_for(
                client.files.delete(file_id, extra_headers=self.extra_headers),
                timeout=self._cleanup_timeout,
            )

        deletion = asyncio.create_task(bounded_delete())
        try:
            try:
                await asyncio.shield(deletion)
            except asyncio.CancelledError:
                try:
                    await deletion
                except asyncio.CancelledError:
                    pass
                except Exception as cleanup_error:
                    _log_sanitized_exception(
                        "remote_cleanup", cleanup_error, logging.WARNING
                    )
                raise
        except asyncio.CancelledError:
            raise
        except Exception as cleanup_error:
            _log_sanitized_exception("remote_cleanup", cleanup_error, logging.WARNING)

    @staticmethod
    def _extract_response_text(response: Any) -> str:
        parts = []
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", None) != "message":
                continue
            for content in getattr(item, "content", []) or []:
                if getattr(content, "type", None) == "output_text":
                    text = getattr(content, "text", "")
                    if text:
                        parts.append(text)
        return "\n".join(parts).strip()

    def _record_usage(self, response: Any, duration_seconds: float) -> None:
        try:
            self._emit_usage(response, duration_seconds)
        except Exception as error:
            _log_sanitized_exception("usage", error, logging.DEBUG)

    def _emit_usage(self, response: Any, duration_seconds: float) -> None:
        usage = getattr(response, "usage", None)
        prompt_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        input_details = getattr(usage, "input_tokens_details", None)
        output_details = getattr(usage, "output_tokens_details", None)
        cached_tokens = int(getattr(input_details, "cached_tokens", 0) or 0)
        reasoning_tokens = int(getattr(output_details, "reasoning_tokens", 0) or 0)

        try:
            from openviking.telemetry import (
                get_current_telemetry,
                get_current_telemetry_stage,
            )

            get_current_telemetry().add_token_usage(
                prompt_tokens,
                completion_tokens,
                stage=get_current_telemetry_stage() or "resource_summarize",
                prompt_cached_tokens=cached_tokens,
                completion_reasoning_tokens=reasoning_tokens,
            )
        except Exception as error:
            _log_sanitized_exception("token_telemetry", error, logging.DEBUG)

        try:
            from openviking.metrics.datasources import VLMEventDataSource
            from openviking.observability.context import get_root_observability_context

            root = get_root_observability_context()
            VLMEventDataSource.record_call(
                provider="volcengine",
                model_name=self.model,
                duration_seconds=duration_seconds,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                account_id=root.account_id if root is not None else None,
            )
        except Exception as error:
            _log_sanitized_exception("usage_metrics", error, logging.DEBUG)
