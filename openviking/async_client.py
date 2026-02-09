# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Async OpenViking client implementation.

Supports both embedded mode (LocalClient) and HTTP mode (HTTPClient).
"""

import os
import threading
from typing import Any, Dict, List, Optional, Union

from openviking.client import HTTPClient, LocalClient, Session
from openviking.client.base import BaseClient
from openviking.service.debug_service import SystemStatus
from openviking.utils import get_logger
from openviking.utils.config import OpenVikingConfig

logger = get_logger(__name__)


class AsyncOpenViking:
    """
    OpenViking main client class (Asynchronous).

    Supports three deployment modes:
    - Embedded mode: Uses local VikingVectorIndex storage and auto-starts AGFS subprocess (singleton)
    - Service mode: Connects to remote VikingVectorIndex and AGFS services (not singleton)
    - HTTP mode: Connects to remote OpenViking Server via HTTP API (not singleton)

    Examples:
        # 1. Embedded mode (auto-starts local services)
        client = AsyncOpenViking(path="./data")
        await client.initialize()

        # 2. Service mode (connects to remote services)
        client = AsyncOpenViking(
            vectordb_url="http://localhost:5000",
            agfs_url="http://localhost:8080",
            user="alice"
        )
        await client.initialize()

        # 3. HTTP mode (connects to OpenViking Server)
        client = AsyncOpenViking(
            url="http://localhost:8000",
            api_key="your-api-key",
            user="alice"
        )
        await client.initialize()

        # 4. Using Config Object for advanced configuration
        from openviking.utils.config import OpenVikingConfig
        from openviking.utils.config import StorageConfig, AGFSConfig, VectorDBBackendConfig

        config = OpenVikingConfig(
            storage=StorageConfig(
                agfs=AGFSConfig(
                    backend="local",
                    path="./custom_data",
                ),
                vectordb=VectorDBBackendConfig(
                    backend="local",
                    path="./custom_data",
                )
            )
        )

        client = AsyncOpenViking(config=config)
        await client.initialize()
    """

    _instance: Optional["AsyncOpenViking"] = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        # HTTP mode: no singleton
        url = kwargs.get("url") or os.environ.get("OPENVIKING_URL")
        if url:
            return object.__new__(cls)

        # Service mode: no singleton
        vectordb_url = kwargs.get("vectordb_url")
        agfs_url = kwargs.get("agfs_url")
        if vectordb_url and agfs_url:
            return object.__new__(cls)

        # Embedded mode: use singleton
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = object.__new__(cls)
        return cls._instance

    def __init__(
        self,
        path: Optional[str] = None,
        url: Optional[str] = None,
        api_key: Optional[str] = None,
        vectordb_url: Optional[str] = None,
        agfs_url: Optional[str] = None,
        user: Optional[str] = None,
        config: Optional[OpenVikingConfig] = None,
        **kwargs,
    ):
        """
        Initialize OpenViking client.

        Args:
            path: Local storage path for embedded mode.
            url: OpenViking Server URL for HTTP mode.
            api_key: API key for HTTP mode authentication.
            vectordb_url: Remote VectorDB service URL for service mode.
            agfs_url: Remote AGFS service URL for service mode.
            user: Username for session management.
            config: OpenVikingConfig object for advanced configuration.
            **kwargs: Additional configuration parameters.
        """
        # Singleton guard for repeated initialization
        if hasattr(self, "_singleton_initialized") and self._singleton_initialized:
            return

        self.user = user or "default"
        self._initialized = False
        self._singleton_initialized = True

        # Environment variable fallback for HTTP mode
        url = url or os.environ.get("OPENVIKING_URL")
        api_key = api_key or os.environ.get("OPENVIKING_API_KEY")

        # Create the appropriate client - only _client, no _service
        if url:
            # HTTP mode
            self._client: BaseClient = HTTPClient(url=url, api_key=api_key, user=user)
        else:
            # Local/Service mode - LocalClient creates and owns the OpenVikingService
            self._client: BaseClient = LocalClient(
                path=path,
                vectordb_url=vectordb_url,
                agfs_url=agfs_url,
                user=user,
                config=config,
            )
            # Get user from the client's service
            self.user = self._client._user

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

    # ============= Resource methods =============

    async def add_resource(
        self,
        path: str,
        target: Optional[str] = None,
        reason: str = "",
        instruction: str = "",
        wait: bool = False,
        timeout: float = None,
    ) -> Dict[str, Any]:
        """Add resource to OpenViking (only supports resources scope).

        Args:
            wait: Whether to wait for semantic extraction and vectorization to complete
            timeout: Wait timeout in seconds
        """
        await self._ensure_initialized()
        return await self._client.add_resource(
            path=path,
            target=target,
            reason=reason,
            instruction=instruction,
            wait=wait,
            timeout=timeout,
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
            limit: Max results
            filter: Metadata filters

        Returns:
            FindResult
        """
        await self._ensure_initialized()
        session_id = session.session_id if session else None
        return await self._client.search(
            query=query,
            target_uri=target_uri,
            session_id=session_id,
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
        return await self._client.ls(uri, recursive=recursive, simple=simple)

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

    async def tree(self, uri: str) -> Dict:
        """Get directory tree"""
        await self._ensure_initialized()
        return await self._client.tree(uri)

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
