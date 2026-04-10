# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for OpenAIRerankClient extra_headers support."""
from unittest.mock import Mock, patch
import pytest

from openviking_cli.utils.config.rerank_config import RerankConfig
from openviking.models.rerank.openai_rerank import OpenAIRerankClient


def test_openai_rerank_client_init_with_extra_headers():
    """Test that OpenAIRerankClient accepts and stores extra_headers."""
    client = OpenAIRerankClient(
        api_key="test-key",
        api_base="https://api.example.com/v1",
        model_name="gpt-4",
        extra_headers={"x-gw-apikey": "Bearer real-key"}
    )

    assert client.extra_headers == {"x-gw-apikey": "Bearer real-key"}


def test_openai_rerank_client_init_without_extra_headers():
    """Test that OpenAIRerankClient defaults to empty dict when extra_headers is None."""
    client = OpenAIRerankClient(
        api_key="test-key",
        api_base="https://api.example.com/v1",
        model_name="gpt-4",
        extra_headers=None
    )

    assert client.extra_headers == {}


def test_openai_rerank_from_config_with_extra_headers():
    """Test that from_config correctly extracts extra_headers from RerankConfig."""
    config = RerankConfig(
        model="gpt-4",
        api_key="test-key",
        api_base="https://api.example.com/v1",
        extra_headers={"x-custom": "value"}
    )

    client = OpenAIRerankClient.from_config(config)

    assert client.extra_headers == {"x-custom": "value"}


def test_openai_rerank_from_config_without_extra_headers():
    """Test that from_config handles None extra_headers correctly."""
    config = RerankConfig(
        model="gpt-4",
        api_key="test-key",
        api_base="https://api.example.com/v1"
    )

    client = OpenAIRerankClient.from_config(config)

    assert client.extra_headers == {}
