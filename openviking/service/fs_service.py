# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
File System Service for OpenViking.

Provides file system operations: ls, mkdir, rm, mv, tree, stat, read, abstract, overview, grep, glob.
"""

from typing import Any, Dict, List, Optional

from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import NotInitializedError
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class FSService:
    """File system operations service."""

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

    async def ls(
        self,
        uri: str,
        recursive: bool = False,
        simple: bool = False,
        output: str = "original",
        abs_limit: int = 256,
        show_all_hidden: bool = False,
        node_limit: int = 1000,
    ) -> List[Any]:
        """List directory contents.

        Args:
            uri: Viking URI
            recursive: List all subdirectories recursively
            simple: Return only relative path list
            output: str = "original" or "agent"
            abs_limit: int = 256 if output == "agent" else ignore
            show_all_hidden: bool = False (list all hidden files, like -a)
            node_limit: int = 1000 (maximum number of nodes to list)
        """
        viking_fs = self._ensure_initialized()

        if recursive:
            entries = await viking_fs.tree(
                uri,
                output=output,
                abs_limit=abs_limit,
                show_all_hidden=show_all_hidden,
                node_limit=node_limit,
            )
        else:
            entries = await viking_fs.ls(
                uri, output=output, abs_limit=abs_limit, show_all_hidden=show_all_hidden
            )

        if simple:
            return [e.get("rel_path", e.get("name", e.get("uri", ""))) for e in entries]
        return entries

    async def mkdir(self, uri: str) -> None:
        """Create directory."""
        viking_fs = self._ensure_initialized()
        await viking_fs.mkdir(uri)

    async def rm(self, uri: str, recursive: bool = False) -> None:
        """Remove resource."""
        viking_fs = self._ensure_initialized()
        await viking_fs.rm(uri, recursive=recursive)

    async def mv(self, from_uri: str, to_uri: str) -> None:
        """Move resource."""
        viking_fs = self._ensure_initialized()
        await viking_fs.mv(from_uri, to_uri)

    async def tree(
        self,
        uri: str,
        output: str = "original",
        abs_limit: int = 128,
        show_all_hidden: bool = False,
        node_limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Get directory tree."""
        viking_fs = self._ensure_initialized()
        return await viking_fs.tree(
            uri,
            output=output,
            abs_limit=abs_limit,
            show_all_hidden=show_all_hidden,
            node_limit=node_limit,
        )

    async def stat(self, uri: str) -> Dict[str, Any]:
        """Get resource status."""
        viking_fs = self._ensure_initialized()
        return await viking_fs.stat(uri)

    async def read(self, uri: str) -> str:
        """Read file content."""
        viking_fs = self._ensure_initialized()
        return await viking_fs.read_file(uri)

    async def abstract(self, uri: str) -> str:
        """Read L0 abstract (.abstract.md)."""
        viking_fs = self._ensure_initialized()
        return await viking_fs.abstract(uri)

    async def overview(self, uri: str) -> str:
        """Read L1 overview (.overview.md)."""
        viking_fs = self._ensure_initialized()
        return await viking_fs.overview(uri)

    async def grep(self, uri: str, pattern: str, case_insensitive: bool = False) -> Dict:
        """Content search."""
        viking_fs = self._ensure_initialized()
        return await viking_fs.grep(uri, pattern, case_insensitive=case_insensitive)

    async def glob(self, pattern: str, uri: str = "viking://") -> Dict:
        """File pattern matching."""
        viking_fs = self._ensure_initialized()
        return await viking_fs.glob(pattern, uri=uri)
