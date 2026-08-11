# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.models.vlm.backends.volcengine_vlm import VolcEngineVLM


def _response(text: str):
    return SimpleNamespace(
        id="response-1",
        status="completed",
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


async def test_audio_media_request_and_remote_cleanup(tmp_path, monkeypatch):
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
    assert upload["extra_headers"]["x-request-id"] == "request-1"
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


async def test_video_media_request_uses_configured_fps(tmp_path, monkeypatch):
    path = tmp_path / "clip.mov"
    path.write_bytes(b"video")
    ark = _ark("video result")
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
    assert ark.responses.create.await_args.kwargs["input"][0]["content"][0] == {
        "type": "input_video",
        "file_id": "file-1",
    }
    ark.files.delete.assert_awaited_once()


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
