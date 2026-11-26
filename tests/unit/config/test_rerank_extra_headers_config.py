# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest
from pydantic import ValidationError

from openviking_cli.utils.config.rerank_config import RerankConfig


@pytest.mark.parametrize("headers", ["invalid", {"X-Gateway-Key": 123}])
def test_rerank_config_rejects_non_string_header_mapping(headers):
    with pytest.raises(ValidationError, match="extra_headers"):
        RerankConfig(
            provider="openai",
            api_key="test-key",
            api_base="https://gateway.example.com/rerank",
            extra_headers=headers,
        )
