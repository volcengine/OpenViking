# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

# Optional Qdrant support (requires qdrant-client)
try:
    from openviking.storage.vectordb.project.qdrant_project import (
        QdrantProject,
        get_or_create_qdrant_project,
    )

    __all__ = ["QdrantProject", "get_or_create_qdrant_project"]
except ImportError:
    __all__ = []
