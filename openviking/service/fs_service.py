# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
File System Service for OpenViking.

Provides file system operations: ls, mkdir, rm, mv, tree, stat, read, abstract, overview, grep, glob.
"""

from typing import Any, Dict, List, Optional

from openviking.core.directories import get_context_type_for_uri
from openviking.privacy import UserPrivacyConfigService, get_skill_name_from_uri, restore_skill_content
from openviking.core.uri_validation import validate_optional_viking_uri, validate_viking_uri
from openviking.server.identity import RequestContext
from openviking.storage.content_write import ContentWriteCoordinator
from openviking.storage.viking_fs import VikingFS
from openviking.utils.embedding_utils import vectorize_directory_meta
from openviking_cli.exceptions import NotInitializedError
from openviking_cli.utils import VikingURI, get_logger

logger = get_logger(__name__)


class FSService:
    """File system operations service."""

    def __init__(
        self,
        viking_fs: Optional[VikingFS] = None,
        privacy_config_service: Optional[UserPrivacyConfigService] = None,
    ):
        self._viking_fs = viking_fs
        self._privacy_config_service = privacy_config_service

    def set_dependencies(
        self,
        viking_fs: VikingFS,
        privacy_config_service: Optional[UserPrivacyConfigService] = None,
    ) -> None:
        """Set service dependencies (for deferred initialization)."""
        self._viking_fs = viking_fs
        self._privacy_config_service = privacy_config_service

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
        uri = validate_viking_uri(uri)

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
                    uri,
                    ctx=ctx,
                    output="original",
                    show_all_hidden=show_all_hidden,
                    node_limit=node_limit,
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
                node_limit=node_limit,
            )
        return entries

    async def mkdir(
        self,
        uri: str,
        ctx: RequestContext,
        description: Optional[str] = None,
    ) -> None:
        """Create directory."""
        uri = validate_viking_uri(uri)
        viking_fs = self._ensure_initialized()
        await viking_fs.mkdir(uri, ctx=ctx)
        abstract = self._normalize_directory_description(description)
        if not abstract:
            return

        directory_uri, abstract_uri = self._resolve_directory_uris(uri)
        await viking_fs.write_file(abstract_uri, abstract, ctx=ctx)
        await vectorize_directory_meta(
            uri=directory_uri,
            abstract=abstract,
            overview="",
            context_type=get_context_type_for_uri(directory_uri),
            ctx=ctx,
            include_overview=False,
        )

    @staticmethod
    def _normalize_directory_description(description: Optional[str]) -> Optional[str]:
        if description is None:
            return None
        abstract = description.strip()
        return abstract or None

    @staticmethod
    def _resolve_directory_uris(uri: str) -> tuple[str, str]:
        abstract_uri = VikingURI(uri).join(".abstract.md").uri
        directory_uri = VikingURI(abstract_uri).parent.uri
        return directory_uri, abstract_uri

    async def rm(self, uri: str, ctx: RequestContext, recursive: bool = False) -> None:
        """Remove resource."""
        uri = validate_viking_uri(uri)
        viking_fs = self._ensure_initialized()
        await viking_fs.rm(uri, recursive=recursive, ctx=ctx)

    async def mv(self, from_uri: str, to_uri: str, ctx: RequestContext) -> None:
        """Move resource."""
        from_uri = validate_viking_uri(from_uri, field_name="from_uri")
        to_uri = validate_viking_uri(to_uri, field_name="to_uri")
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
        uri = validate_viking_uri(uri)
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
        uri = validate_viking_uri(uri)
        return await viking_fs.stat(uri, ctx=ctx)

    async def read(self, uri: str, ctx: RequestContext, offset: int = 0, limit: int = -1) -> str:
        """Read file content."""
        viking_fs = self._ensure_initialized()
        uri = validate_viking_uri(uri)
        content = await viking_fs.read_file(uri, ctx=ctx)
        skill_name = get_skill_name_from_uri(uri)
        if skill_name and self._privacy_config_service:
            current = await self._privacy_config_service.get_current(
                ctx=ctx,
                category="skill",
                target_key=skill_name,
            )
            if current:
                content = restore_skill_content(content, skill_name, current.values)

        if offset == 0 and limit == -1:
            return content
        lines = content.splitlines(keepends=True)
        sliced = lines[offset:] if limit == -1 else lines[offset : offset + limit]
        return "".join(sliced)

    async def abstract(self, uri: str, ctx: RequestContext) -> str:
        """Read L0 abstract (.abstract.md)."""
        viking_fs = self._ensure_initialized()
        uri = validate_viking_uri(uri)
        return await viking_fs.abstract(uri, ctx=ctx)

    async def overview(self, uri: str, ctx: RequestContext) -> str:
        """Read L1 overview (.overview.md)."""
        viking_fs = self._ensure_initialized()
        uri = validate_viking_uri(uri)
        return await viking_fs.overview(uri, ctx=ctx)

    async def grep(
        self,
        uri: str,
        pattern: str,
        ctx: RequestContext,
        exclude_uri: Optional[str] = None,
        case_insensitive: bool = False,
        node_limit: Optional[int] = None,
        level_limit: int = 5,
    ) -> Dict:
        """Content search."""
        viking_fs = self._ensure_initialized()
        uri = validate_viking_uri(uri)
        exclude_uri = validate_optional_viking_uri(exclude_uri, field_name="exclude_uri") or None
        return await viking_fs.grep(
            uri,
            pattern,
            exclude_uri=exclude_uri,
            case_insensitive=case_insensitive,
            node_limit=node_limit,
            level_limit=level_limit,
            ctx=ctx,
        )

    async def glob(
        self,
        pattern: str,
        ctx: RequestContext,
        uri: str = "viking://",
        node_limit: Optional[int] = None,
    ) -> Dict:
        """File pattern matching."""
        viking_fs = self._ensure_initialized()
        uri = validate_viking_uri(uri)
        return await viking_fs.glob(pattern, uri=uri, node_limit=node_limit, ctx=ctx)

    async def read_file_bytes(self, uri: str, ctx: RequestContext) -> bytes:
        """Read file as raw bytes."""
        viking_fs = self._ensure_initialized()
        uri = validate_viking_uri(uri)
        return await viking_fs.read_file_bytes(uri, ctx=ctx)

    async def write(
        self,
        uri: str,
        content: str,
        ctx: RequestContext,
        mode: str = "replace",
        wait: bool = False,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Write to an existing file and refresh semantics/vectors."""
        uri = validate_viking_uri(uri)
        viking_fs = self._ensure_initialized()
        coordinator = ContentWriteCoordinator(viking_fs=viking_fs)
        return await coordinator.write(
            uri=uri,
            content=content,
            ctx=ctx,
            mode=mode,
            wait=wait,
            timeout=timeout,
        )
