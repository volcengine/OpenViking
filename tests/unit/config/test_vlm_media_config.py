# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest
from pydantic import ValidationError

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


def test_vlm_media_rejects_invalid_runtime_limits():
    invalid_values = [
        ({"max_concurrent": 0}, "max_concurrent"),
        ({"file_processing_timeout": 0}, "file_processing_timeout"),
        ({"file_poll_interval": 0}, "file_poll_interval"),
        ({"video_fps": 0.19}, "video_fps"),
        ({"video_fps": 5.01}, "video_fps"),
    ]

    for media, field in invalid_values:
        with pytest.raises(ValidationError, match=field):
            VLMConfig(media=media)


def test_vlm_media_options_reach_backend_config():
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
