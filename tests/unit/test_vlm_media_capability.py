# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
from pathlib import Path

import pytest

from openviking.models.vlm.base import FailoverVLM, MultiCredentialVLM, VLMBase
from openviking_cli.utils.config.vlm_config import VLMConfig


class DummyVLM(VLMBase):
    def get_completion(self, *args, **kwargs):
        return ""

    async def get_completion_async(self, *args, **kwargs):
        return ""

    def get_vision_completion(self, *args, **kwargs):
        return ""

    async def get_vision_completion_async(self, *args, **kwargs):
        return ""


class RecordingMediaVLM(DummyVLM):
    def __init__(self, *, provider: str = "test", error: Exception | None = None):
        super().__init__({"provider": provider, "model": "media-model"})
        self.active = 0
        self.max_active = 0
        self.calls = []
        self.error = error

    def supports_media(self, *, media_type: str, filename: str, size_bytes: int) -> bool:
        return media_type == "audio" and filename.endswith(".mp3") and size_bytes <= 10

    async def get_media_completion_async(
        self,
        *,
        prompt: str,
        media_path: Path,
        filename: str,
        media_type: str,
    ) -> str:
        if self.error is not None:
            raise self.error
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.calls.append((prompt, media_path, filename, media_type))
        await asyncio.sleep(0.01)
        self.active -= 1
        return "media summary"


def test_vlm_base_reports_media_as_unsupported_by_default():
    vlm = DummyVLM({"provider": "test", "model": "text-only"})

    assert vlm.supports_media(media_type="audio", filename="meeting.mp3", size_bytes=4) is False


async def test_vlm_base_rejects_media_calls_by_default(tmp_path):
    from openviking.models.vlm.base import UnsupportedMediaInputError

    vlm = DummyVLM({"provider": "test", "model": "text-only"})

    with pytest.raises(UnsupportedMediaInputError, match="test"):
        await vlm.get_media_completion_async(
            prompt="summarize",
            media_path=tmp_path / "meeting.mp3",
            filename="meeting.mp3",
            media_type="audio",
        )


async def test_vlm_config_rejects_media_when_disabled(tmp_path):
    from openviking.models.vlm.base import UnsupportedMediaInputError

    config = VLMConfig()

    assert config.supports_media(media_type="audio", filename="meeting.mp3", size_bytes=4) is False
    with pytest.raises(UnsupportedMediaInputError, match="disabled"):
        await config.get_media_completion_async(
            prompt="summarize",
            media_path=tmp_path / "meeting.mp3",
            filename="meeting.mp3",
            media_type="audio",
        )


async def test_vlm_config_delegates_media_and_limits_concurrency(tmp_path):
    backend = RecordingMediaVLM()
    config = VLMConfig(
        provider="volcengine",
        api_key="test-key",
        model="media-model",
        media={"enabled": True, "max_concurrent": 1},
    )
    config._vlm_instance = backend

    assert config.supports_media(
        media_type="audio",
        filename="meeting.mp3",
        size_bytes=4,
    )

    results = await asyncio.gather(
        *[
            config.get_media_completion_async(
                prompt=f"summarize-{index}",
                media_path=tmp_path / "meeting.mp3",
                filename="meeting.mp3",
                media_type="audio",
            )
            for index in range(2)
        ]
    )

    assert results == ["media summary", "media summary"]
    assert backend.max_active == 1


async def test_failover_vlm_routes_media_to_capable_backup(tmp_path):
    primary = DummyVLM({"provider": "text-only", "model": "primary"})
    backup = RecordingMediaVLM(provider="media-backup")
    vlm = FailoverVLM(primary, backup)

    assert vlm.supports_media(
        media_type="audio",
        filename="meeting.mp3",
        size_bytes=4,
    )
    result = await vlm.get_media_completion_async(
        prompt="summarize",
        media_path=tmp_path / "meeting.mp3",
        filename="meeting.mp3",
        media_type="audio",
    )

    assert result == "media summary"
    assert len(backup.calls) == 1


async def test_failover_vlm_uses_only_media_capable_primary_while_backup_is_active(
    tmp_path,
):
    primary = RecordingMediaVLM(provider="media-primary")
    backup = DummyVLM({"provider": "text-only-backup", "model": "backup"})
    vlm = FailoverVLM(primary, backup)
    assert vlm._switcher.record_primary_failure(RuntimeError("401 unauthorized"))
    assert vlm.is_using_backup

    result = await vlm.get_media_completion_async(
        prompt="summarize",
        media_path=tmp_path / "meeting.mp3",
        filename="meeting.mp3",
        media_type="audio",
    )

    assert result == "media summary"
    assert len(primary.calls) == 1
    assert not vlm.is_using_backup


async def test_multi_credential_vlm_skips_unsupported_media_backend(tmp_path):
    text_only = DummyVLM({"provider": "text-only", "model": "primary"})
    media = RecordingMediaVLM(provider="media")
    vlm = MultiCredentialVLM(
        [text_only, media],
        credential_ids=["text", "media"],
    )

    result = await vlm.get_media_completion_async(
        prompt="summarize",
        media_path=tmp_path / "meeting.mp3",
        filename="meeting.mp3",
        media_type="audio",
    )

    assert result == "media summary"
    assert len(media.calls) == 1


async def test_multi_credential_vlm_retries_media_on_credential_failure(tmp_path):
    unauthorized = RecordingMediaVLM(
        provider="media-primary",
        error=RuntimeError("401 unauthorized"),
    )
    backup = RecordingMediaVLM(provider="media-backup")
    vlm = MultiCredentialVLM(
        [unauthorized, backup],
        credential_ids=["primary", "backup"],
    )

    result = await vlm.get_media_completion_async(
        prompt="summarize",
        media_path=tmp_path / "meeting.mp3",
        filename="meeting.mp3",
        media_type="audio",
    )

    assert result == "media summary"
    assert len(backup.calls) == 1
