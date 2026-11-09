# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from volcenginesdkarkruntime import AsyncArk

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


@pytest.mark.parametrize("media_type, suffix", [("audio", ".mp3"), ("video", ".mov")])
@pytest.mark.parametrize(
    "extra_body",
    [None, {"service_tier": "flex", "thinking": {"type": "disabled"}}, {"store": True}],
)
async def test_media_request_and_remote_cleanup(
    tmp_path, monkeypatch, media_type, suffix, extra_body
):
    path = tmp_path / f"media{suffix}"
    path.write_bytes(b"local media fixture")
    requests = []

    def send(request):
        requests.append(request)
        if request.url.path == "/v3/responses":
            return httpx.Response(
                200,
                json={
                    "id": "response-1",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "media result"}],
                        }
                    ],
                },
            )
        return httpx.Response(200, json={"id": "file-1", "status": "active", "deleted": True})

    vlm = _vlm(extra_headers={"x-request-id": "request-1"}, extra_request_body=extra_body)
    async with AsyncArk(
        api_key="key",
        base_url="https://ark.example/v3",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(send)),
    ) as ark:
        monkeypatch.setattr(vlm, "get_async_client", lambda: ark)
        result = await vlm.get_media_completion_async(
            prompt="analyze media", media_path=path, filename=path.name, media_type=media_type
        )

    assert result == "media result"
    assert [(r.method, r.url.path) for r in requests] == [
        ("POST", "/v3/files"),
        ("GET", "/v3/files/file-1"),
        ("POST", "/v3/responses"),
        ("DELETE", "/v3/files/file-1"),
    ]
    request = json.loads(requests[2].content)
    assert request["model"] == "media-model"
    assert request["input"][0]["content"] == [
        {"type": f"input_{media_type}", "file_id": "file-1"},
        {"type": "input_text", "text": "analyze media"},
    ]
    assert request["max_output_tokens"] == 1024
    assert request["store"] is (extra_body or {}).get("store", False)
    for key, value in (extra_body or {}).items():
        assert request[key] == value
        assert key.encode() not in requests[0].content
    assert vlm.extra_request_body == (extra_body or {})
    for sent in (requests[0], requests[2], requests[3]):
        assert sent.headers["x-request-id"] == "request-1"
    assert not requests[1].content and not json.loads(requests[3].content)


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
