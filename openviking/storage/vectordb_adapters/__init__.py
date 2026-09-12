# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""VectorDB backend collection adapter package."""

from .base import CollectionAdapter
from .factory import create_collection_adapter
from .http_adapter import HttpCollectionAdapter
from .local_adapter import LocalCollectionAdapter
from .opengauss_adapter import OpenGaussCollectionAdapter
from .qdrant_adapter import QdrantCollectionAdapter
from .vikingdb_private_adapter import VikingDBPrivateCollectionAdapter
from .volcengine_adapter import VolcengineCollectionAdapter

__all__ = [
    "CollectionAdapter",
    "LocalCollectionAdapter",
    "HttpCollectionAdapter",
    "OpenGaussCollectionAdapter",
    "QdrantCollectionAdapter",
    "VolcengineCollectionAdapter",
    "VikingDBPrivateCollectionAdapter",
    "create_collection_adapter",
]
