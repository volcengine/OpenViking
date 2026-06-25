# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Knowledge deduplication based on content hashing.
Prevents duplicate knowledge from being written to viking:// storage.
"""
import hashlib
from typing import Set

from openviking.daemon.models import ExtractedKnowledge
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class KnowledgeDeduplicator:
    """Deduplicates knowledge items based on MD5 content hash."""

    def __init__(self):
        self.seen_hashes: Set[str] = set()

    def is_duplicate(self, knowledge: ExtractedKnowledge) -> bool:
        """Check if this knowledge is a duplicate of something already seen."""
        content_hash = hashlib.md5(
            knowledge.content.encode("utf-8")
        ).hexdigest()

        if content_hash in self.seen_hashes:
            logger.debug("Duplicate knowledge skipped: %s", knowledge.title)
            return True

        self.seen_hashes.add(content_hash)
        return False

    def clear(self):
        """Clear the dedup cache."""
        self.seen_hashes.clear()
