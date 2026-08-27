# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Local keyword retrieval via a SQLite FTS5 sidecar.

The keyword sidecar provides backend-owned, search-time BM25 recall for
deployments without a remote VikingDB full-text index. It is an accelerator
only: the final matching decision is always made against the on-disk content
(grep) or by the vector retrieval pipeline (find/search hybrid).
"""

from openviking.storage.keywordfs.config import (
    CjkMode,
    ContentSource,
    HybridRetrievalConfig,
    KeywordConfig,
    TokenizerMode,
)
from openviking.storage.keywordfs.keyword_fs import KeywordFS
from openviking.storage.keywordfs.keyword_msg import KeywordMsg

__all__ = [
    "CjkMode",
    "ContentSource",
    "HybridRetrievalConfig",
    "KeywordConfig",
    "KeywordFS",
    "KeywordMsg",
    "TokenizerMode",
]
