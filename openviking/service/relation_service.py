# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Relation Service for OpenViking.

Provides relation management operations: relations, link, unlink.
"""

from typing import Any, Dict, List, Optional, Union

from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import NotInitializedError
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class RelationService:
    """Relation management service."""

    def __init__(self, viking_fs: Optional[VikingFS] = None):
        self._viking_fs = viking_fs

    def set_viking_fs(self, viking_fs: VikingFS) -> None:
        """Set VikingFS instance (for deferred initialization)."""
        self._viking_fs = viking_fs

    def _ensure_initialized(self) -> VikingFS:
        """Ensure VikingFS is initialized."""
        if not self._viking_fs:
            raise NotInitializedError("VikingFS")
        return self._viking_fs

    async def relations(self, uri: str) -> List[Dict[str, Any]]:
        """Get relations (returns [{"uri": "...", "reason": "..."}, ...])."""
        viking_fs = self._ensure_initialized()
        return await viking_fs.relations(uri)

    async def link(self, from_uri: str, uris: Union[str, List[str]], reason: str = "") -> None:
        """Create link (single or multiple).

        Args:
            from_uri: Source URI
            uris: Target URI or list of URIs
            reason: Reason for linking
        """
        viking_fs = self._ensure_initialized()
        await viking_fs.link(from_uri, uris, reason)

    async def unlink(self, from_uri: str, uri: str) -> None:
        """Remove link (remove specified URI from uris).

        Args:
            from_uri: Source URI
            uri: Target URI to remove
        """
        viking_fs = self._ensure_initialized()
        await viking_fs.unlink(from_uri, uri)
