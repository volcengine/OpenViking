# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import gc
import weakref

import pytest
from pydantic import ValidationError

from openviking_cli.utils.config.open_viking_config import OpenVikingConfig
from openviking_cli.utils.config.vlm_config import VLMConfig


def _configured_vlm(**overrides) -> VLMConfig:
    values = {
        "provider": "volcengine",
        "api_key": "test-key",
        "model": "test-model",
    }
    values.update(overrides)
    return VLMConfig(**values)


def test_vlm_media_is_disabled_by_default():
    config = VLMConfig()

    assert config.media.enabled is False
    assert config.media.max_concurrent == 2
    assert config.media.file_processing_timeout == 1800.0
    assert config.media.file_poll_interval == 3.0
    assert config.media.video_fps == 1.0


@pytest.mark.parametrize(
    ("media", "field"),
    [
        ({"max_concurrent": 0}, "max_concurrent"),
        ({"file_processing_timeout": 0}, "file_processing_timeout"),
        ({"file_poll_interval": 0}, "file_poll_interval"),
        ({"video_fps": 0.19}, "video_fps"),
        ({"video_fps": 5.01}, "video_fps"),
    ],
)
def test_vlm_media_rejects_invalid_runtime_limits(media, field):
    with pytest.raises(ValidationError, match=field):
        VLMConfig(media=media)


def test_vlm_media_options_reach_single_credential_backend_config():
    config = _configured_vlm(
        media={
            "enabled": True,
            "max_concurrent": 3,
            "file_processing_timeout": 900,
            "file_poll_interval": 1.5,
            "video_fps": 0.5,
        }
    )

    backend_config = config._build_vlm_config_dict_for_credential(config.credentials[0])

    assert backend_config["media"] == {
        "enabled": True,
        "max_concurrent": 3,
        "file_processing_timeout": 900.0,
        "file_poll_interval": 1.5,
        "video_fps": 0.5,
    }


def test_vlm_media_options_reach_legacy_backend_config():
    config = _configured_vlm(media={"enabled": True, "video_fps": 2.0})

    backend_config = config._build_vlm_config_dict()

    assert backend_config["media"]["enabled"] is True
    assert backend_config["media"]["video_fps"] == 2.0


def test_openviking_config_accepts_only_nested_vlm_media_configuration():
    config = OpenVikingConfig.from_dict(
        {
            "vlm": {
                "provider": "volcengine",
                "api_key": "test-key",
                "model": "test-model",
                "media": {"enabled": True},
            }
        }
    )

    assert config.vlm.media.enabled is True
    assert not hasattr(config, "media_understanding")

    with pytest.raises(ValueError, match="media_understanding"):
        OpenVikingConfig.from_dict({"media_understanding": {}})


def test_vlm_media_semaphore_does_not_retain_closed_event_loop():
    config = _configured_vlm(media={"enabled": True, "max_concurrent": 1})

    async def contend_for_semaphore():
        semaphore = config._get_media_semaphore()
        await semaphore.acquire()
        waiter = asyncio.create_task(semaphore.acquire())
        await asyncio.sleep(0)
        semaphore.release()
        await waiter
        semaphore.release()
        return weakref.ref(asyncio.get_running_loop()), weakref.ref(semaphore)

    loop_ref, semaphore_ref = asyncio.run(contend_for_semaphore())
    gc.collect()

    assert loop_ref() is None
    assert semaphore_ref() is None
    assert len(config._media_semaphores) == 0
