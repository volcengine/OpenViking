# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.models.vlm.backends.volcengine_vlm import VolcEngineVLM


def _response(text: str, *, status: str = "completed"):
    return SimpleNamespace(
        id="response-1",
        status=status,
        output=[
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text=text)],
            )
        ],
        usage=SimpleNamespace(
            input_tokens=120,
            output_tokens=40,
            input_tokens_details=SimpleNamespace(cached_tokens=2),
            output_tokens_details=SimpleNamespace(reasoning_tokens=3),
        ),
    )


def _ark(response_text: str = "media result"):
    return SimpleNamespace(
        files=SimpleNamespace(
            create=AsyncMock(return_value=SimpleNamespace(id="file-1")),
            wait_for_processing=AsyncMock(return_value=SimpleNamespace(status="active")),
            delete=AsyncMock(),
        ),
        responses=SimpleNamespace(create=AsyncMock(return_value=_response(response_text))),
    )


def _vlm(**overrides) -> VolcEngineVLM:
    config = {
        "provider": "volcengine",
        "api_key": "key",
        "api_base": "https://ark.example/v3",
        "model": "media-model",
        "max_tokens": 1024,
        "media": {
            "enabled": True,
            "max_concurrent": 2,
            "file_processing_timeout": 900.0,
            "file_poll_interval": 1.5,
            "video_fps": 0.5,
        },
    }
    config.update(overrides)
    return VolcEngineVLM(config)


def test_volcengine_advertises_only_supported_media_inputs():
    vlm = _vlm()

    assert vlm.supports_media(media_type="audio", filename="meeting.mp3", size_bytes=1024)
    assert vlm.supports_media(media_type="video", filename="clip.MOV", size_bytes=1024)
    assert not vlm.supports_media(media_type="audio", filename="meeting.flac", size_bytes=1024)
    assert not vlm.supports_media(
        media_type="video",
        filename="clip.mp4",
        size_bytes=512 * 1024 * 1024 + 1,
    )


async def test_audio_media_call_reuses_vlm_client_and_configuration(tmp_path, monkeypatch):
    path = tmp_path / "meeting.mp3"
    path.write_bytes(b"ID3-audio")
    ark = _ark("audio result")
    vlm = _vlm(extra_headers={"x-request-id": "request-1"})
    monkeypatch.setattr(vlm, "get_async_client", lambda: ark)

    result = await vlm.get_media_completion_async(
        prompt="analyze audio",
        media_path=path,
        filename=path.name,
        media_type="audio",
    )

    assert result == "audio result"
    upload = ark.files.create.await_args.kwargs
    assert upload["purpose"] == "user_data"
    assert upload["preprocess_configs"] is None
    assert upload["extra_headers"] == {
        "x-request-id": "request-1",
        "X-Client-Request-Id": ("ToB-direct,OpenViking_Service,openviking-service_cn-beijing"),
    }
    ark.files.wait_for_processing.assert_awaited_once_with(
        "file-1",
        poll_interval=1.5,
        max_wait_seconds=900.0,
    )
    request = ark.responses.create.await_args.kwargs
    assert request["model"] == "media-model"
    assert request["input"][0]["content"] == [
        {"type": "input_audio", "file_id": "file-1"},
        {"type": "input_text", "text": "analyze audio"},
    ]
    assert request["max_output_tokens"] == 1024
    assert request["store"] is False
    ark.files.delete.assert_awaited_once_with(
        "file-1",
        extra_headers=upload["extra_headers"],
    )


async def test_video_media_call_passes_fps_and_cleanup_failure_preserves_result(
    tmp_path, monkeypatch
):
    path = tmp_path / "clip.mov"
    path.write_bytes(b"video")
    ark = _ark("video result")
    ark.files.delete.side_effect = RuntimeError("cleanup unavailable")
    vlm = _vlm()
    monkeypatch.setattr(vlm, "get_async_client", lambda: ark)

    result = await vlm.get_media_completion_async(
        prompt="analyze video",
        media_path=path,
        filename=path.name,
        media_type="video",
    )

    assert result == "video result"
    assert ark.files.create.await_args.kwargs["preprocess_configs"] == {"video": {"fps": 0.5}}


async def test_failed_file_processing_is_terminal_and_cleans_remote_file(tmp_path, monkeypatch):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"video")
    ark = _ark()
    ark.files.wait_for_processing.return_value = SimpleNamespace(
        status="failed",
        error=SimpleNamespace(message="unsupported codec"),
    )
    vlm = _vlm(max_retries=3)
    monkeypatch.setattr(vlm, "get_async_client", lambda: ark)

    with pytest.raises(RuntimeError, match="unsupported codec"):
        await vlm.get_media_completion_async(
            prompt="analyze video",
            media_path=path,
            filename=path.name,
            media_type="video",
        )

    assert ark.files.create.await_count == 1
    ark.files.delete.assert_awaited_once()


async def test_media_usage_updates_vlm_token_tracker(tmp_path, monkeypatch):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"video")
    ark = _ark("tracked result")
    vlm = _vlm()
    monkeypatch.setattr(vlm, "get_async_client", lambda: ark)

    result = await vlm.get_media_completion_async(
        prompt="analyze video",
        media_path=path,
        filename=path.name,
        media_type="video",
    )

    assert result == "tracked result"
    usage = vlm.get_token_usage()
    assert usage["total_usage"]["prompt_tokens"] == 120
    assert usage["total_usage"]["completion_tokens"] == 40
    assert usage["usage_by_model"]["media-model"]["total_usage"]["call_count"] == 1


async def test_transient_upload_error_retries_with_same_vlm_client(tmp_path, monkeypatch):
    class ServiceUnavailable(RuntimeError):
        status_code = 503

    path = tmp_path / "meeting.mp3"
    path.write_bytes(b"ID3-audio")
    ark = _ark("retried result")
    ark.files.create.side_effect = [
        ServiceUnavailable("provider response must not be logged"),
        SimpleNamespace(id="file-2"),
    ]
    vlm = _vlm(max_retries=1)
    monkeypatch.setattr(vlm, "get_async_client", lambda: ark)
    monkeypatch.setattr(
        "openviking.utils.model_retry.asyncio.sleep",
        AsyncMock(),
    )

    result = await vlm.get_media_completion_async(
        prompt="analyze audio",
        media_path=path,
        filename=path.name,
        media_type="audio",
    )

    assert result == "retried result"
    assert ark.files.create.await_count == 2
    ark.files.delete.assert_awaited_once_with(
        "file-2",
        extra_headers=vlm.extra_headers,
    )


async def test_cancellation_still_cleans_up_uploaded_file(tmp_path, monkeypatch):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"video")
    ark = _ark()
    processing_started = asyncio.Event()

    async def wait_forever(*_args, **_kwargs):
        processing_started.set()
        await asyncio.Future()

    ark.files.wait_for_processing.side_effect = wait_forever
    vlm = _vlm()
    monkeypatch.setattr(vlm, "get_async_client", lambda: ark)

    task = asyncio.create_task(
        vlm.get_media_completion_async(
            prompt="analyze video",
            media_path=path,
            filename=path.name,
            media_type="video",
        )
    )
    await processing_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    ark.files.delete.assert_awaited_once_with(
        "file-1",
        extra_headers=vlm.extra_headers,
    )


async def test_cleanup_error_preserves_original_processing_error(tmp_path, monkeypatch):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"video")
    ark = _ark()
    ark.files.wait_for_processing.return_value = SimpleNamespace(
        status="failed",
        error=SimpleNamespace(message="unsupported codec"),
    )
    ark.files.delete.side_effect = RuntimeError("cleanup unavailable")
    vlm = _vlm()
    monkeypatch.setattr(vlm, "get_async_client", lambda: ark)

    with pytest.raises(RuntimeError, match="unsupported codec"):
        await vlm.get_media_completion_async(
            prompt="analyze video",
            media_path=path,
            filename=path.name,
            media_type="video",
        )

    ark.files.delete.assert_awaited_once()
