# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest
from pydantic import ValidationError

from openviking_cli.utils.config.vectordb_config import CuVSConfig


def test_cuvs_filter_cache_defaults_and_disable_value():
    assert CuVSConfig().filter_cache_size == 16
    assert CuVSConfig(filter_cache_size=0).filter_cache_size == 0


def test_cuvs_filter_cache_rejects_negative_size():
    with pytest.raises(ValidationError, match="filter_cache_size"):
        CuVSConfig(filter_cache_size=-1)
