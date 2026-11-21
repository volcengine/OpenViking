# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for rerank extra_headers configuration."""

import pytest
from pydantic import ValidationError

from openviking_cli.utils.config.rerank_config import RerankConfig


def test_rerank_config_extra_headers_type_validation():
    config = {
        "provider": "openai",
        "api_base": "https://gateway.example/v1/rerank",
        "api_key": "test-key",
    }

    with pytest.raises(ValidationError, match="extra_headers"):
        RerankConfig.model_validate({**config, "extra_headers": "invalid"})

    with pytest.raises(ValidationError, match="extra_headers.header"):
        RerankConfig.model_validate({**config, "extra_headers": {"header": 123}})
