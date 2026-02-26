# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Static registry for vector backend drivers."""

from __future__ import annotations

from typing import Callable, Dict, Type

from openviking.storage.vector_store.driver import VectorStoreDriver

_DRIVER_REGISTRY: Dict[str, Type[VectorStoreDriver]] = {}


def register_driver(name: str) -> Callable[[Type[VectorStoreDriver]], Type[VectorStoreDriver]]:
    """Register a vector backend driver class by backend name."""

    def decorator(cls: Type[VectorStoreDriver]) -> Type[VectorStoreDriver]:
        _DRIVER_REGISTRY[name] = cls
        return cls

    return decorator


def get_driver_class(name: str) -> Type[VectorStoreDriver]:
    """Resolve registered driver class for backend name."""
    if name not in _DRIVER_REGISTRY:
        raise ValueError(
            f"Vector backend {name} is not registered. "
            f"Available backends: {sorted(_DRIVER_REGISTRY)}"
        )
    return _DRIVER_REGISTRY[name]


def list_registered_backends() -> list[str]:
    return sorted(_DRIVER_REGISTRY)
