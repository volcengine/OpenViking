# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Ark Files/Responses helpers used by the VolcEngine VLM backend."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openviking.utils.media_limits import MAX_MEDIA_FILE_BYTES
from openviking.utils.model_retry import retry_async

if TYPE_CHECKING:
    from .volcengine_vlm import VolcEngineVLM

logger = logging.getLogger(__name__)

MEDIA_EXTENSIONS = {
    "audio": frozenset({".mp3", ".wav", ".aac", ".m4a"}),
    "video": frozenset({".mp4", ".avi", ".mov"}),
}
_CLEANUP_TIMEOUT_SECONDS = 5.0
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


def _safe_attribute(error: BaseException, name: str) -> Any:
    try:
        return getattr(error, name, None)
    except BaseException:
        return None


def _structured_status_code(error: BaseException) -> int | None:
    value = _safe_attribute(error, "status_code")
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
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


def _is_retryable_media_error(error: Exception) -> bool:
    try:
        chain = tuple(_exception_chain(error))
        if any(isinstance(item, ArkFileProcessingFailedError) for item in chain):
            return False

        structured_statuses = []
        has_structured_429 = False
        for item in chain:
            status_code = _structured_status_code(item)
            if status_code is None:
                continue
            structured_statuses.append(status_code)
            if status_code not in _RETRYABLE_HTTP_STATUSES:
                return False
            has_structured_429 = has_structured_429 or status_code == 429

        if has_structured_429 and any(
            any(marker in _structured_error_text(item) for marker in _PERMANENT_429_MARKERS)
            for item in chain
        ):
            return False
        if structured_statuses:
            return True

        return any(
            isinstance(
                item,
                (TimeoutError, ConnectionError, *_NATIVE_TRANSIENT_ERROR_TYPES),
            )
            for item in chain
        )
    except BaseException:
        return False


def _first_structured_value(error: BaseException, name: str) -> str:
    for item in _exception_chain(error):
        value = _safe_attribute(item, name)
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


def supports_media(*, media_type: str, filename: str, size_bytes: int) -> bool:
    extensions = MEDIA_EXTENSIONS.get(media_type)
    if extensions is None:
        return False
    return Path(filename).suffix.lower() in extensions and 0 <= size_bytes <= MAX_MEDIA_FILE_BYTES


async def understand_media(
    vlm: VolcEngineVLM,
    *,
    prompt: str,
    media_path: Path,
    filename: str,
    media_type: str,
) -> str:
    size_bytes = media_path.stat().st_size
    if not supports_media(
        media_type=media_type,
        filename=filename,
        size_bytes=size_bytes,
    ):
        raise ValueError(f"Unsupported or oversized {media_type} input: {filename}")

    async def attempt_with_sanitized_logging() -> str:
        try:
            return await _attempt(
                vlm,
                prompt=prompt,
                media_path=media_path,
                media_type=media_type,
            )
        except Exception as error:
            _log_sanitized_exception("request", error, logging.WARNING)
            raise

    return await retry_async(
        attempt_with_sanitized_logging,
        max_retries=vlm.max_retries,
        is_retryable=_is_retryable_media_error,
        logger=None,
    )


async def _attempt(
    vlm: VolcEngineVLM,
    *,
    prompt: str,
    media_path: Path,
    media_type: str,
) -> str:
    file_id = None
    client = vlm.get_async_client()
    media_options = dict(vlm.config.get("media") or {})
    poll_interval = float(media_options.get("file_poll_interval", 3.0))
    processing_timeout = float(media_options.get("file_processing_timeout", 1800.0))
    video_fps = float(media_options.get("video_fps", 1.0))
    preprocess = {"video": {"fps": video_fps}} if media_type == "video" else None

    try:
        with media_path.open("rb") as upload_file:
            uploaded = await client.files.create(
                file=upload_file,
                purpose="user_data",
                preprocess_configs=preprocess,
                extra_headers=vlm.extra_headers,
            )
        file_id = uploaded.id
        processed = await client.files.wait_for_processing(
            file_id,
            poll_interval=poll_interval,
            max_wait_seconds=processing_timeout,
        )
        if processed.status != "active":
            if processed.status == "failed":
                processing_error = getattr(processed, "error", None)
                message = getattr(processing_error, "message", None) or "Ark file processing failed"
                raise ArkFileProcessingFailedError(message)
            raise RuntimeError("Ark file processing did not become active")

        request = {
            "model": vlm.model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": ("input_audio" if media_type == "audio" else "input_video"),
                            "file_id": file_id,
                        },
                        {"type": "input_text", "text": prompt},
                    ],
                }
            ],
            "store": False,
            "extra_headers": vlm.extra_headers,
        }
        if vlm.max_tokens is not None:
            request["max_output_tokens"] = vlm.max_tokens

        started = time.monotonic()
        response = await client.responses.create(**request)
        if getattr(response, "status", None) != "completed":
            raise RuntimeError("Ark media response did not complete")
        text = _extract_response_text(response)
        if not text:
            raise RuntimeError("Ark media response contained no output text")
        _record_usage(vlm, response, time.monotonic() - started)
        return text
    finally:
        if file_id:
            await _delete_remote_file(vlm, file_id)


async def _delete_remote_file(vlm: VolcEngineVLM, file_id: str) -> None:
    client = vlm.get_async_client()

    async def bounded_delete() -> None:
        await asyncio.wait_for(
            client.files.delete(file_id, extra_headers=vlm.extra_headers),
            timeout=_CLEANUP_TIMEOUT_SECONDS,
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
                    "remote_cleanup",
                    cleanup_error,
                    logging.WARNING,
                )
            raise
    except asyncio.CancelledError:
        raise
    except Exception as cleanup_error:
        _log_sanitized_exception("remote_cleanup", cleanup_error, logging.WARNING)


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


def _record_usage(
    vlm: VolcEngineVLM,
    response: Any,
    duration_seconds: float,
) -> None:
    try:
        usage = getattr(response, "usage", None)
        input_details = getattr(usage, "input_tokens_details", None)
        output_details = getattr(usage, "output_tokens_details", None)
        vlm.update_token_usage(
            model_name=vlm.model,
            provider=vlm.provider,
            prompt_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            duration_seconds=duration_seconds,
            prompt_cached_tokens=int(getattr(input_details, "cached_tokens", 0) or 0),
            completion_reasoning_tokens=int(getattr(output_details, "reasoning_tokens", 0) or 0),
        )
    except Exception as error:
        _log_sanitized_exception("usage", error, logging.DEBUG)
