# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest
from pydantic import ValidationError

from openviking.storage.vectordb_adapters.local_adapter import (
    CuVSCollectionAdapter,
    LocalCollectionAdapter,
)
from openviking_cli.utils.config.vectordb_config import CuVSConfig


def test_cuvs_filter_cache_defaults_and_disable_value():
    assert CuVSConfig().filter_cache_size == 16
    assert CuVSConfig(filter_cache_size=0).filter_cache_size == 0


def test_cuvs_filter_cache_rejects_negative_size():
    with pytest.raises(ValidationError, match="filter_cache_size"):
        CuVSConfig(filter_cache_size=-1)


def test_cuvs_adapter_preserves_native_int8_index_default():
    native = LocalCollectionAdapter("context", "", "default")
    cuvs = CuVSCollectionAdapter("context", "", "default", {})
    arguments = {
        "index_name": "default",
        "distance": "cosine",
        "use_sparse": False,
        "sparse_weight": 0.0,
        "scalar_index_fields": [],
    }

    native_meta = native.build_default_index_meta(**arguments)
    cuvs_meta = cuvs.build_default_index_meta(**arguments)

    assert native_meta["VectorIndex"]["Quant"] == "int8"
    assert cuvs_meta["VectorIndex"]["Quant"] == "int8"
