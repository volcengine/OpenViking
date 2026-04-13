# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for rerank extra_headers configuration."""
import pytest
from pydantic import ValidationError


def test_rerank_config_with_extra_headers():
    """Test that extra_headers can be parsed from config."""
    from openviking_cli.utils.config.rerank_config import RerankConfig

    config_data = {
        "model": "gpt-4",
        "api_key": "test-key",
        "api_base": "https://api.example.com/v1",
        "extra_headers": {
            "x-gw-apikey": "Bearer real-key",
            "X-Custom-Header": "custom-value"
        }
    }

    config = RerankConfig(**config_data)

    assert config.extra_headers == {
        "x-gw-apikey": "Bearer real-key",
        "X-Custom-Header": "custom-value"
    }


def test_rerank_config_without_extra_headers():
    """Test that extra_headers defaults to None when not provided."""
    from openviking_cli.utils.config.rerank_config import RerankConfig

    config_data = {
        "model": "gpt-4",
        "api_key": "test-key",
        "api_base": "https://api.example.com/v1"
    }

    config = RerankConfig(**config_data)

    assert config.extra_headers is None


def test_rerank_config_extra_headers_type_validation():
    """Test that extra_headers must be a dict with string keys and values."""
    from openviking_cli.utils.config.rerank_config import RerankConfig

    # Invalid: not a dict
    with pytest.raises(ValidationError):
        RerankConfig(
            model="gpt-4",
            api_key="test-key",
            extra_headers="invalid"
        )

    # Invalid: dict with non-string value
    with pytest.raises(ValidationError):
        RerankConfig(
            model="gpt-4",
            api_key="test-key",
            extra_headers={"header": 123}
        )
