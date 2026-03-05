# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Adapter registry and factory entrypoints."""

from __future__ import annotations

from .base import CollectionAdapter
from .http_adapter import HttpCollectionAdapter
from .local_adapter import LocalCollectionAdapter
from .vikingdb_private_adapter import VikingDBPrivateCollectionAdapter
from .volcengine_adapter import VolcengineCollectionAdapter

_ADAPTER_REGISTRY: dict[str, type[CollectionAdapter]] = {
    "local": LocalCollectionAdapter,
    "http": HttpCollectionAdapter,
    "volcengine": VolcengineCollectionAdapter,
    "vikingdb": VikingDBPrivateCollectionAdapter,
}


def create_collection_adapter(config) -> CollectionAdapter:
    """Unified factory entrypoint for backend-specific collection adapters."""
    backend = config.backend
    adapter_cls = _ADAPTER_REGISTRY.get(backend)

    # If not in registry, try to load dynamically as a class path
    if adapter_cls is None and "." in backend:
        try:
            import importlib
            module_name, class_name = backend.rsplit(".", 1)
            module = importlib.import_module(module_name)
            potential_cls = getattr(module, class_name)
            if issubclass(potential_cls, CollectionAdapter):
                adapter_cls = potential_cls
        except (ImportError, AttributeError, TypeError):
            # Fallback to raising error if dynamic loading fails
            pass

    if adapter_cls is None:
        raise ValueError(
            f"Vector backend {config.backend} is not supported. "
            f"Available backends: {sorted(_ADAPTER_REGISTRY)}"
        )
    return adapter_cls.from_config(config)
