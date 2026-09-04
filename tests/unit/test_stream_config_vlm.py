# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""VLM streaming is not a supported configuration contract."""

import pytest
from pydantic import ValidationError

from openviking_cli.utils.config.vlm_config import VLMConfig


@pytest.mark.parametrize(
    "config",
    [
        {
            "provider": "openai",
            "model": "test-model",
            "api_key": "test-key",
            "stream": False,
        },
        {
            "model": "test-model",
            "providers": {"openai": {"api_key": "test-key", "stream": True}},
        },
        {
            "model": "test-model",
            "credentials": [
                {
                    "provider": "openai",
                    "model": "test-model",
                    "api_key": "test-key",
                    "stream": True,
                }
            ],
        },
        {
            "provider": "openai",
            "model": "test-model",
            "api_key": "test-key",
            "extra_request_body": {"stream": True},
        },
        {
            "model": "test-model",
            "providers": {
                "openai": {
                    "api_key": "test-key",
                    "extra_request_body": {"stream": True},
                }
            },
        },
    ],
)
def test_vlm_config_rejects_stream(config):
    with pytest.raises(ValidationError, match="stream.*not supported"):
        VLMConfig(**config)
