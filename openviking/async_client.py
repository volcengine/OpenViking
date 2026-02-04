# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Async OpenViking client implementation.

This is a compatibility layer that delegates to OpenVikingService.
"""

import threading
from typing import Any, Dict, List, Optional

from openviking.service.core import OpenVikingService
from openviking.service.debug_service import SystemStatus
from openviking.session import Session
from openviking.utils import get_logger
from openviking.utils.config import OpenVikingConfig

logger = get_logger(__name__)


class AsyncOpenViking:
    """
    OpenViking main client class (Asynchronous).

    Supports two deployment modes:
    - Embedded mode: Uses local VikingVectorIndex storage and auto-starts AGFS subprocess (singleton)
    - Service mode: Connects to remote VikingVectorIndex and AGFS services (not singleton)

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

        # 3. Using Config Object for advanced configuration
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
            vectordb_url: Remote VectorDB service URL for service mode.
            agfs_url: Remote AGFS service URL for service mode.
            user: Username for session management.
            config: OpenVikingConfig object for advanced configuration.
            **kwargs: Additional configuration parameters.
        """
        # Singleton guard for repeated initialization
        if hasattr(self, "_singleton_initialized") and self._singleton_initialized:
            return

        # Create the service layer
        self._service = OpenVikingService(
            path=path,
            vectordb_url=vectordb_url,
            agfs_url=agfs_url,
            user=user,
            config=config,
        )

        self.user = self._service.user
        self._initialized = False
        self._singleton_initialized = True

    # ============= Properties for backward compatibility =============

    @property
    def viking_fs(self):
        return self._service.viking_fs

    @property
    def _viking_fs(self):
        return self._service.viking_fs

    @property
    def _vikingdb_manager(self):
        return self._service.vikingdb_manager

    @property
    def _session_compressor(self):
        return self._service.session_compressor

    @property
    def _config(self):
        return self._service._config

    @property
    def _agfs_manager(self):
        return self._service._agfs_manager

    @property
    def _resource_processor(self):
        return self._service._resource_processor

    @property
    def _skill_processor(self):
        return self._service._skill_processor

    # ============= Lifecycle methods =============

    async def initialize(self) -> None:
        """Initialize OpenViking storage and indexes."""
        await self._service.initialize()
        self._initialized = True

    async def _ensure_initialized(self):
        """Ensure storage collections are initialized."""
        if not self._initialized:
            await self.initialize()

    async def close(self) -> None:
        """Close OpenViking and release resources."""
        await self._service.close()
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
        return Session(
            viking_fs=self._service.viking_fs,
            vikingdb_manager=self._service.vikingdb_manager,
            session_compressor=self._service.session_compressor,
            user=self.user,
            session_id=session_id,
        )

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
        like: viking://resources/github/volcengine/OpenViking
        Args:
            wait: Whether to wait for semantic extraction and vectorization to complete
            timeout: Wait timeout in seconds
        """
        await self._ensure_initialized()
        return await self._service.resources.add_resource(
            path=path,
            target=target,
            reason=reason,
            instruction=instruction,
            wait=wait,
            timeout=timeout,
        )

    async def wait_processed(self, timeout: float = None) -> Dict[str, Any]:
        """Wait for all queued processing to complete."""
        return await self._service.resources.wait_processed(timeout=timeout)

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
        return await self._service.resources.add_skill(
            data=data,
            wait=wait,
            timeout=timeout,
        )

    # ============= Search methods =============

    async def search(
        self,
        query: str,
        target_uri: str = "",
        session: Optional["Session"] = None,
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
        return await self._service.search.search(
            query=query,
            target_uri=target_uri,
            session=session,
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
        return await self._service.search.find(
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
        return await self._service.fs.abstract(uri)

    async def overview(self, uri: str) -> str:
        """Read L1 overview (.overview.md)"""
        await self._ensure_initialized()
        return await self._service.fs.overview(uri)

    async def read(self, uri: str) -> str:
        """Read file content"""
        await self._ensure_initialized()
        return await self._service.fs.read(uri)

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
        return await self._service.fs.ls(uri, recursive=recursive, simple=simple)

    async def rm(self, uri: str, recursive: bool = False) -> None:
        """Remove resource"""
        await self._ensure_initialized()
        await self._service.fs.rm(uri, recursive=recursive)

    async def grep(self, uri: str, pattern: str, case_insensitive: bool = False) -> Dict:
        """Content search"""
        await self._ensure_initialized()
        return await self._service.fs.grep(uri, pattern, case_insensitive=case_insensitive)

    async def glob(self, pattern: str, uri: str = "viking://") -> Dict:
        """File pattern matching"""
        await self._ensure_initialized()
        return await self._service.fs.glob(pattern, uri=uri)

    async def mv(self, from_uri: str, to_uri: str) -> None:
        """Move resource"""
        await self._ensure_initialized()
        await self._service.fs.mv(from_uri, to_uri)

    async def tree(self, uri: str) -> Dict:
        """Get directory tree"""
        await self._ensure_initialized()
        return await self._service.fs.tree(uri)

    async def mkdir(self, uri: str) -> None:
        """Create directory"""
        await self._ensure_initialized()
        await self._service.fs.mkdir(uri)

    async def stat(self, uri: str) -> Dict:
        """Get resource status"""
        await self._ensure_initialized()
        return await self._service.fs.stat(uri)

    # ============= Relation methods =============

    async def relations(self, uri: str) -> List[Dict[str, Any]]:
        """Get relations (returns [{"uri": "...", "reason": "..."}, ...])"""
        await self._ensure_initialized()
        return await self._service.relations.relations(uri)

    async def link(self, from_uri: str, uris: Any, reason: str = "") -> None:
        """
        Create link (single or multiple).

        Args:
            from_uri: Source URI
            uris: Target URI or list of URIs
            reason: Reason for linking
        """
        await self._ensure_initialized()
        await self._service.relations.link(from_uri, uris, reason)

    async def unlink(self, from_uri: str, uri: str) -> None:
        """
        Remove link (remove specified URI from uris).

        Args:
            from_uri: Source URI
            uri: Target URI to remove
        """
        await self._ensure_initialized()
        await self._service.relations.unlink(from_uri, uri)

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
        return await self._service.pack.export_ovpack(uri, to)

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
        return await self._service.pack.import_ovpack(file_path, parent, force=force, vectorize=vectorize)

    # ============= Debug methods =============

    def get_status(self) -> SystemStatus:
        """Get system status.

        Returns:
            SystemStatus containing health status of all components.
        """
        return self._service.debug.get_system_status()

    def is_healthy(self) -> bool:
        """Quick health check.

        Returns:
            True if all components are healthy, False otherwise.
        """
        return self._service.debug.is_healthy()
