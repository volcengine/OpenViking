# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest
from pydantic import ValidationError

from openviking.storage.vectordb_adapters.local_adapter import (
    LocalCollectionAdapter,
)
from openviking_cli.utils.config.vectordb_config import CuVSConfig, VectorDBBackendConfig


def test_cuvs_filter_cache_defaults_and_disable_value():
    assert CuVSConfig().dtype == "float32"
    assert CuVSConfig(dtype="float16").dtype == "float16"
    assert CuVSConfig().max_concurrent_gpu_searches == 1
    assert CuVSConfig().filter_cache_size == 16
    assert CuVSConfig(filter_cache_size=0).filter_cache_size == 0


def test_cuvs_filter_cache_rejects_negative_size():
    with pytest.raises(ValidationError, match="dtype"):
        CuVSConfig(dtype="int8")
    with pytest.raises(ValidationError, match="filter_cache_size"):
        CuVSConfig(filter_cache_size=-1)
    with pytest.raises(ValidationError, match="max_concurrent_gpu_searches"):
        CuVSConfig(max_concurrent_gpu_searches=0)


def test_cuvs_auto_mode_is_opt_in_and_validates_memory_guardrails():
    config = CuVSConfig()
    assert config.auto_enable is False
    assert config.auto_memory_reserve_mb == 1024
    assert config.auto_memory_safety_factor == 2.0
    assert config.auto_filter_native_threshold == 2000
    assert config.auto_path_filter_native_threshold == 200
    assert config.auto_background_rebuild is False
    assert config.auto_rebuild_debounce_ms == 500

    with pytest.raises(ValidationError, match="auto_memory_reserve_mb"):
        CuVSConfig(auto_memory_reserve_mb=-1)
    with pytest.raises(ValidationError, match="auto_memory_safety_factor"):
        CuVSConfig(auto_memory_safety_factor=0.5)
    with pytest.raises(ValidationError, match="auto_filter_native_threshold"):
        CuVSConfig(auto_filter_native_threshold=-1)
    with pytest.raises(ValidationError, match="auto_path_filter_native_threshold"):
        CuVSConfig(auto_path_filter_native_threshold=-1)
    with pytest.raises(ValidationError, match="auto_rebuild_debounce_ms"):
        CuVSConfig(auto_rebuild_debounce_ms=-1)


def test_local_adapter_only_passes_auto_cuvs_config_when_enabled():
    default_adapter = LocalCollectionAdapter.from_config(VectorDBBackendConfig(backend="local"))
    assert default_adapter.mode == "local"
    assert default_adapter._collection_config == {}

    auto_adapter = LocalCollectionAdapter.from_config(
        VectorDBBackendConfig(
            backend="local",
            cuvs={
                "auto_enable": True,
                "auto_memory_reserve_mb": 512,
                "auto_memory_safety_factor": 1.5,
                "auto_filter_native_threshold": 1000,
                "auto_path_filter_native_threshold": 100,
            },
        )
    )
    dense_search = auto_adapter._collection_config["dense_search"]
    assert auto_adapter.mode == "local"
    assert dense_search["backend"] == "auto_cuvs"
    assert dense_search["auto_enable"] is True
    assert dense_search["auto_memory_reserve_mb"] == 512
    assert dense_search["auto_memory_safety_factor"] == 1.5
    assert dense_search["auto_filter_native_threshold"] == 1000
    assert dense_search["auto_path_filter_native_threshold"] == 100
