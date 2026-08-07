# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Adapter registry and factory entrypoints."""

from __future__ import annotations

import importlib
from typing import Any

from .base import CollectionAdapter
from .http_adapter import HttpCollectionAdapter
from .local_adapter import CuVSCollectionAdapter, LocalCollectionAdapter
from .opengauss_adapter import OpenGaussCollectionAdapter
from .qdrant_adapter import QdrantCollectionAdapter
from .vikingdb_private_adapter import VikingDBPrivateCollectionAdapter
from .volcengine_adapter import VolcengineCollectionAdapter

_ADAPTER_REGISTRY: dict[str, type[CollectionAdapter]] = {
    "local": LocalCollectionAdapter,
    "cuvs": CuVSCollectionAdapter,
    "http": HttpCollectionAdapter,
    "opengauss": OpenGaussCollectionAdapter,
    "qdrant": QdrantCollectionAdapter,
    "volcengine": VolcengineCollectionAdapter,
    "vikingdb": VikingDBPrivateCollectionAdapter,
}


def resolve_collection_adapter_class(backend: str) -> type[CollectionAdapter]:
    """Resolve adapter class without instantiating the backend."""
    adapter_cls = _ADAPTER_REGISTRY.get(backend)

    # If not in registry, try to load dynamically as a class path
    if adapter_cls is None and "." in backend:
        try:
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
            f"Vector backend {backend} is not supported. "
            f"Available backends: {sorted(_ADAPTER_REGISTRY)}"
        )
    return adapter_cls


def backend_uses_content_field(config_or_backend: Any) -> bool:
    """Whether a vector backend stores the full-text content field."""
    backend = getattr(config_or_backend, "backend", config_or_backend)
    adapter_cls = resolve_collection_adapter_class(str(backend))
    return bool(getattr(adapter_cls, "USE_CONTENT_FIELD", False))


def create_collection_adapter(config) -> CollectionAdapter:
    """Unified factory entrypoint for backend-specific collection adapters."""
    adapter_cls = resolve_collection_adapter_class(config.backend)
    return adapter_cls.from_config(config)
