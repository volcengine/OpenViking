# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
File System Service for OpenViking.

Provides file system operations: ls, mkdir, rm, mv, tree, stat, read, abstract, overview, grep, glob.
"""

from typing import Any, Dict, List, Optional

from openviking.server.identity import RequestContext
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
        ctx: RequestContext,
        recursive: bool = False,
        simple: bool = False,
        output: str = "original",
        abs_limit: int = 256,
        show_all_hidden: bool = False,
        node_limit: int = 1000,
        level_limit: int = 3,
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

        if simple:
            # Only return URIs — skip expensive abstract fetching to save tokens
            if recursive:
                entries = await viking_fs.tree(
                    uri,
                    ctx=ctx,
                    output="original",
                    show_all_hidden=show_all_hidden,
                    node_limit=node_limit,
                    level_limit=level_limit,
                )
            else:
                entries = await viking_fs.ls(
                    uri, ctx=ctx, output="original", show_all_hidden=show_all_hidden
                )
            return [e.get("uri", "") for e in entries]

        if recursive:
            entries = await viking_fs.tree(
                uri,
                ctx=ctx,
                output=output,
                abs_limit=abs_limit,
                show_all_hidden=show_all_hidden,
                node_limit=node_limit,
                level_limit=level_limit,
            )
        else:
            entries = await viking_fs.ls(
                uri,
                ctx=ctx,
                output=output,
                abs_limit=abs_limit,
                show_all_hidden=show_all_hidden,
            )
        return entries

    async def mkdir(self, uri: str, ctx: RequestContext) -> None:
        """Create directory."""
        viking_fs = self._ensure_initialized()
        await viking_fs.mkdir(uri, ctx=ctx)

    async def rm(self, uri: str, ctx: RequestContext, recursive: bool = False) -> None:
        """Remove resource."""
        viking_fs = self._ensure_initialized()
        await viking_fs.rm(uri, recursive=recursive, ctx=ctx)

    async def mv(self, from_uri: str, to_uri: str, ctx: RequestContext) -> None:
        """Move resource."""
        viking_fs = self._ensure_initialized()
        await viking_fs.mv(from_uri, to_uri, ctx=ctx)

    async def tree(
        self,
        uri: str,
        ctx: RequestContext,
        output: str = "original",
        abs_limit: int = 128,
        show_all_hidden: bool = False,
        node_limit: int = 1000,
        level_limit: int = 3,
    ) -> List[Dict[str, Any]]:
        """Get directory tree."""
        viking_fs = self._ensure_initialized()
        return await viking_fs.tree(
            uri,
            ctx=ctx,
            output=output,
            abs_limit=abs_limit,
            show_all_hidden=show_all_hidden,
            node_limit=node_limit,
            level_limit=level_limit,
        )

    async def stat(self, uri: str, ctx: RequestContext) -> Dict[str, Any]:
        """Get resource status."""
        viking_fs = self._ensure_initialized()
        return await viking_fs.stat(uri, ctx=ctx)

    async def read(self, uri: str, ctx: RequestContext, offset: int = 0, limit: int = -1) -> str:
        """Read file content."""
        viking_fs = self._ensure_initialized()
        return await viking_fs.read_file(uri, offset=offset, limit=limit, ctx=ctx)

    async def abstract(self, uri: str, ctx: RequestContext) -> str:
        """Read L0 abstract (.abstract.md)."""
        viking_fs = self._ensure_initialized()
        return await viking_fs.abstract(uri, ctx=ctx)

    async def overview(self, uri: str, ctx: RequestContext) -> str:
        """Read L1 overview (.overview.md)."""
        viking_fs = self._ensure_initialized()
        return await viking_fs.overview(uri, ctx=ctx)

    async def grep(
        self, uri: str, pattern: str, ctx: RequestContext, case_insensitive: bool = False
    ) -> Dict:
        """Content search."""
        viking_fs = self._ensure_initialized()
        return await viking_fs.grep(uri, pattern, case_insensitive=case_insensitive, ctx=ctx)

    async def glob(self, pattern: str, ctx: RequestContext, uri: str = "viking://") -> Dict:
        """File pattern matching."""
        viking_fs = self._ensure_initialized()
        return await viking_fs.glob(pattern, uri=uri, ctx=ctx)
