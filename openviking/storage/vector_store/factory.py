# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Factory for vector backend drivers."""

from __future__ import annotations

from openviking.storage.vector_store.driver import VectorStoreDriver
from openviking.storage.vector_store.registry import get_driver_class


def create_driver(config) -> VectorStoreDriver:
    """Create backend driver from `VectorDBBackendConfig` without backend if/else."""
    # Ensure all static registrations are loaded.
    import openviking.storage.vector_store.drivers  # noqa: F401

    driver_cls = get_driver_class(config.backend)
    return driver_cls.from_config(config)
