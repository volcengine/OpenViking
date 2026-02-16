# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Async OpenViking client implementation (embedded mode only).

For HTTP mode, use AsyncHTTPClient or SyncHTTPClient.
"""

import threading
from typing import Any, Dict, List, Optional, Union

from openviking.client import LocalClient, Session
from openviking.service.debug_service import SystemStatus
from openviking_cli.client.base import BaseClient
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class AsyncOpenViking:
    """
    OpenViking main client class (Asynchronous, embedded mode only).

    Uses local storage and auto-starts services (singleton).
    For HTTP mode, use AsyncHTTPClient or SyncHTTPClient instead.

    Examples:
        client = AsyncOpenViking(path="./data")
        await client.initialize()
    """

    _instance: Optional["AsyncOpenViking"] = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = object.__new__(cls)
        return cls._instance

    def __init__(
        self,
        path: Optional[str] = None,
        **kwargs,
    ):
        """
        Initialize OpenViking client (embedded mode).

        Args:
            path: Local storage path (overrides ov.conf storage path).
            **kwargs: Additional configuration parameters.
        """
        # Singleton guard for repeated initialization
        if hasattr(self, "_singleton_initialized") and self._singleton_initialized:
            return

        self.user = UserIdentifier.the_default_user()
        self._initialized = False
        self._singleton_initialized = True

        self._client: BaseClient = LocalClient(
            path=path,
        )

    # ============= Lifecycle methods =============

    async def initialize(self) -> None:
        """Initialize OpenViking storage and indexes."""
        await self._client.initialize()
        self._initialized = True

    async def _ensure_initialized(self):
        """Ensure storage collections are initialized."""
        if not self._initialized:
            await self.initialize()

    async def close(self) -> None:
        """Close OpenViking and release resources."""
        await self._client.close()
        self._initialized = False
        self._singleton_initialized = False

    @classmethod
    async def reset(cls) -> None:
        """Reset the singleton instance (mainly for testing)."""
        with cls._lock:
            if cls._instance is not None:
                await cls._instance.close()
                cls._instance._initialized = False
                cls._instance._singleton_initialized = False
                cls._instance = None

    # ============= Session methods =============

    def session(self, session_id: Optional[str] = None) -> Session:
        """
        Create a new session or load an existing one.

        Args:
            session_id: Session ID, creates a new session (auto-generated ID) if None
        """
        return self._client.session(session_id)

    async def create_session(self) -> Dict[str, Any]:
        """Create a new session."""
        await self._ensure_initialized()
        return await self._client.create_session()

    async def list_sessions(self) -> List[Any]:
        """List all sessions."""
        await self._ensure_initialized()
        return await self._client.list_sessions()

    async def get_session(self, session_id: str) -> Dict[str, Any]:
        """Get session details."""
        await self._ensure_initialized()
        return await self._client.get_session(session_id)

    async def delete_session(self, session_id: str) -> None:
        """Delete a session."""
        await self._ensure_initialized()
        await self._client.delete_session(session_id)

    async def add_message(self, session_id: str, role: str, content: str) -> Dict[str, Any]:
        """Add a message to a session."""
        await self._ensure_initialized()
        return await self._client.add_message(session_id=session_id, role=role, content=content)

    async def commit_session(self, session_id: str) -> Dict[str, Any]:
        """Commit a session (archive and extract memories)."""
        await self._ensure_initialized()
        return await self._client.commit_session(session_id)

    # ============= Resource methods =============

    async def add_resource(
        self,
        path: str,
        target: Optional[str] = None,
        reason: str = "",
        instruction: str = "",
        wait: bool = False,
        timeout: float = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Add resource to OpenViking (only supports resources scope).

        Args:
            wait: Whether to wait for semantic extraction and vectorization to complete
            timeout: Wait timeout in seconds
            **kwargs: Extra options forwarded to the parser chain, e.g.
                ``strict``, ``ignore_dirs``, ``include``, ``exclude``.
        """
        await self._ensure_initialized()
        return await self._client.add_resource(
            path=path,
            target=target,
            reason=reason,
            instruction=instruction,
            wait=wait,
            timeout=timeout,
            **kwargs,
        )

    async def wait_processed(self, timeout: float = None) -> Dict[str, Any]:
        """Wait for all queued processing to complete."""
        await self._ensure_initialized()
        return await self._client.wait_processed(timeout=timeout)

    async def add_skill(
        self,
        data: Any,
        wait: bool = False,
        timeout: float = None,
    ) -> Dict[str, Any]:
        """Add skill to OpenViking.

        Args:
            wait: Whether to wait for vectorization to complete
            timeout: Wait timeout in seconds
        """
        await self._ensure_initialized()
        return await self._client.add_skill(
            data=data,
            wait=wait,
            timeout=timeout,
        )

    # ============= Search methods =============

    async def search(
        self,
        query: str,
        target_uri: str = "",
        session: Optional[Union["Session", Any]] = None,
        session_id: Optional[str] = None,
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict] = None,
    ):
        """
        Complex search with session context.

        Args:
            query: Query string
            target_uri: Target directory URI
            session: Session object for context
            session_id: Session ID string (alternative to session object)
            limit: Max results
            filter: Metadata filters

        Returns:
            FindResult
        """
        await self._ensure_initialized()
        sid = session_id or (session.session_id if session else None)
        return await self._client.search(
            query=query,
            target_uri=target_uri,
            session_id=sid,
            limit=limit,
            score_threshold=score_threshold,
            filter=filter,
        )

    async def find(
        self,
        query: str,
        target_uri: str = "",
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict] = None,
    ):
        """Semantic search"""
        await self._ensure_initialized()
        return await self._client.find(
            query=query,
            target_uri=target_uri,
            limit=limit,
            score_threshold=score_threshold,
            filter=filter,
        )

    # ============= FS methods =============

    async def abstract(self, uri: str) -> str:
        """Read L0 abstract (.abstract.md)"""
        await self._ensure_initialized()
        return await self._client.abstract(uri)

    async def overview(self, uri: str) -> str:
        """Read L1 overview (.overview.md)"""
        await self._ensure_initialized()
        return await self._client.overview(uri)

    async def read(self, uri: str) -> str:
        """Read file content"""
        await self._ensure_initialized()
        return await self._client.read(uri)

    async def ls(self, uri: str, **kwargs) -> List[Any]:
        """
        List directory contents.

        Args:
            uri: Viking URI
            simple: Return only relative path list (bool, default: False)
            recursive: List all subdirectories recursively (bool, default: False)
        """
        await self._ensure_initialized()
        recursive = kwargs.get("recursive", False)
        simple = kwargs.get("simple", False)
        output = kwargs.get("output", "original")
        abs_limit = kwargs.get("abs_limit", 256)
        show_all_hidden = kwargs.get("show_all_hidden", True)
        return await self._client.ls(
            uri,
            recursive=recursive,
            simple=simple,
            output=output,
            abs_limit=abs_limit,
            show_all_hidden=show_all_hidden,
        )

    async def rm(self, uri: str, recursive: bool = False) -> None:
        """Remove resource"""
        await self._ensure_initialized()
        await self._client.rm(uri, recursive=recursive)

    async def grep(self, uri: str, pattern: str, case_insensitive: bool = False) -> Dict:
        """Content search"""
        await self._ensure_initialized()
        return await self._client.grep(uri, pattern, case_insensitive=case_insensitive)

    async def glob(self, pattern: str, uri: str = "viking://") -> Dict:
        """File pattern matching"""
        await self._ensure_initialized()
        return await self._client.glob(pattern, uri=uri)

    async def mv(self, from_uri: str, to_uri: str) -> None:
        """Move resource"""
        await self._ensure_initialized()
        await self._client.mv(from_uri, to_uri)

    async def tree(self, uri: str, **kwargs) -> Dict:
        """Get directory tree"""
        await self._ensure_initialized()
        output = kwargs.get("output", "original")
        abs_limit = kwargs.get("abs_limit", 128)
        show_all_hidden = kwargs.get("show_all_hidden", True)
        node_limit = kwargs.get("node_limit", 1000)
        return await self._client.tree(
            uri,
            output=output,
            abs_limit=abs_limit,
            show_all_hidden=show_all_hidden,
            node_limit=node_limit,
        )

    async def mkdir(self, uri: str) -> None:
        """Create directory"""
        await self._ensure_initialized()
        await self._client.mkdir(uri)

    async def stat(self, uri: str) -> Dict:
        """Get resource status"""
        await self._ensure_initialized()
        return await self._client.stat(uri)

    # ============= Relation methods =============

    async def relations(self, uri: str) -> List[Dict[str, Any]]:
        """Get relations (returns [{"uri": "...", "reason": "..."}, ...])"""
        await self._ensure_initialized()
        return await self._client.relations(uri)

    async def link(self, from_uri: str, uris: Any, reason: str = "") -> None:
        """
        Create link (single or multiple).

        Args:
            from_uri: Source URI
            uris: Target URI or list of URIs
            reason: Reason for linking
        """
        await self._ensure_initialized()
        await self._client.link(from_uri, uris, reason)

    async def unlink(self, from_uri: str, uri: str) -> None:
        """
        Remove link (remove specified URI from uris).

        Args:
            from_uri: Source URI
            uri: Target URI to remove
        """
        await self._ensure_initialized()
        await self._client.unlink(from_uri, uri)

    # ============= Pack methods =============

    async def export_ovpack(self, uri: str, to: str) -> str:
        """
        Export specified context path as .ovpack file.

        Args:
            uri: Viking URI
            to: Target file path

        Returns:
            Exported file path
        """
        await self._ensure_initialized()
        return await self._client.export_ovpack(uri, to)

    async def import_ovpack(
        self, file_path: str, parent: str, force: bool = False, vectorize: bool = True
    ) -> str:
        """
        Import local .ovpack file to specified parent path.

        Args:
            file_path: Local .ovpack file path
            parent: Target parent URI (e.g., viking://user/alice/resources/references/)
            force: Whether to force overwrite existing resources (default: False)
            vectorize: Whether to trigger vectorization (default: True)

        Returns:
            Imported root resource URI
        """
        await self._ensure_initialized()
        return await self._client.import_ovpack(file_path, parent, force=force, vectorize=vectorize)

    # ============= Debug methods =============

    def get_status(self) -> Union[SystemStatus, Dict[str, Any]]:
        """Get system status.

        Returns:
            SystemStatus containing health status of all components.
        """
        return self._client.get_status()

    def is_healthy(self) -> bool:
        """Quick health check.

        Returns:
            True if all components are healthy, False otherwise.
        """
        return self._client.is_healthy()

    @property
    def observer(self):
        """Get observer service for component status."""
        return self._client.observer
