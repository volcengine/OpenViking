# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Collection implementations for VikingDB."""

from openviking.storage.vectordb.collection.collection import Collection, ICollection
from openviking.storage.vectordb.collection.http_collection import (
    HttpCollection,
    get_or_create_http_collection,
)
from openviking.storage.vectordb.collection.local_collection import (
    LocalCollection,
    get_or_create_local_collection,
)
from openviking.storage.vectordb.collection.volcengine_collection import (
    VolcengineCollection,
    get_or_create_volcengine_collection,
)

__all__ = [
    "ICollection",
    "Collection",
    "VolcengineCollection",
    "get_or_create_volcengine_collection",
    "HttpCollection",
    "get_or_create_http_collection",
    "LocalCollection",
    "get_or_create_local_collection",
]

# Optional Qdrant support (requires qdrant-client)
try:
    from openviking.storage.vectordb.collection.qdrant_collection import (
        QdrantCollection,
        get_or_create_qdrant_collection,
    )
    __all__.extend(["QdrantCollection", "get_or_create_qdrant_collection"])
except ImportError:
    pass  # qdrant-client not installed
