# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Rerank provider selection and configuration validation."""

import pytest
from pydantic import ValidationError

from openviking.models.rerank import RerankClient
from openviking_cli.utils.config.rerank_config import RerankConfig


def test_vikingdb_credentials_select_vikingdb_without_an_explicit_provider():
    config = RerankConfig(ak="test-ak", sk="test-sk")
    assert type(RerankClient.from_config(config)) is RerankClient


def test_unconfigured_rerank_has_no_client():
    assert RerankClient.from_config(RerankConfig()) is None


def test_openai_requires_api_key_and_api_base():
    with pytest.raises(ValidationError, match="api_key.*api_base"):
        RerankConfig(provider="openai", api_key="key")
    with pytest.raises(ValidationError, match="api_key.*api_base"):
        RerankConfig(provider="openai", api_base="https://example.com/rerank")


def test_unknown_provider_is_rejected():
    with pytest.raises(ValidationError, match="provider"):
        RerankConfig(provider="unknown", api_key="key")


def test_explicit_provider_requires_its_own_credentials():
    with pytest.raises(ValidationError, match="api_key"):
        RerankConfig(provider="cohere", ak="ak", sk="sk")
    with pytest.raises(ValidationError, match="ak.*sk"):
        RerankConfig(provider="vikingdb", api_key="key")
