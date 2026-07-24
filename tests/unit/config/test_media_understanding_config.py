# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
import pytest

from openviking_cli.utils.config.open_viking_config import OpenVikingConfig


def _model(model: str, api_key: str) -> dict:
    return {
        "provider": "volcengine",
        "api_key": api_key,
        "model": model,
    }


def test_media_understanding_is_optional():
    config = OpenVikingConfig.from_dict({})
    assert config.media_understanding.audio is None
    assert config.media_understanding.video is None


def test_audio_and_video_configs_are_independent():
    config = OpenVikingConfig.from_dict(
        {
            "media_understanding": {
                "audio": _model("audio-endpoint", "audio-key"),
                "video": {**_model("video-endpoint", "video-key"), "fps": 0.5},
            }
        }
    )
    assert config.media_understanding.audio.model == "audio-endpoint"
    assert config.media_understanding.audio.api_key == "audio-key"
    assert config.media_understanding.video.model == "video-endpoint"
    assert config.media_understanding.video.api_key == "video-key"
    assert config.media_understanding.video.fps == 0.5


@pytest.mark.parametrize("field", ["provider", "api_key", "model"])
def test_present_media_config_requires_complete_model_fields(field):
    raw = _model("audio-endpoint", "audio-key")
    raw.pop(field)
    with pytest.raises(ValueError, match=field):
        OpenVikingConfig.from_dict({"media_understanding": {"audio": raw}})


def test_phase_one_rejects_non_volcengine_provider():
    with pytest.raises(ValueError, match="volcengine"):
        OpenVikingConfig.from_dict(
            {
                "media_understanding": {
                    "audio": {
                        "provider": "openai",
                        "api_key": "key",
                        "model": "model",
                    }
                }
            }
        )


@pytest.mark.parametrize("fps", [0.19, 5.01])
def test_video_fps_must_match_ark_range(fps):
    with pytest.raises(ValueError, match="fps"):
        OpenVikingConfig.from_dict(
            {"media_understanding": {"video": {**_model("video", "key"), "fps": fps}}}
        )
