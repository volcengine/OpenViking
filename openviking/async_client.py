# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Async OpenViking client implementation.
"""

# Standard library imports
import threading
from typing import Any, Dict, List, Optional

# Local imports - Core modules
from openviking.agfs_manager import AGFSManager
from openviking.core.directories import DirectoryInitializer
from openviking.session import Session
from openviking.session.compressor import SessionCompressor
from openviking.storage import VikingDBManager
from openviking.storage.collection_schemas import init_context_collection
from openviking.storage.local_fs import export_ovpack as local_export_ovpack
from openviking.storage.local_fs import import_ovpack as local_import_ovpack
from openviking.storage.observers import QueueObserver, VikingDBObserver, VLMObserver
from openviking.storage.queuefs import get_queue_manager
from openviking.storage.viking_fs import VikingFS, init_viking_fs
from openviking.utils import get_logger
from openviking.utils.config import OpenVikingConfig, get_openviking_config
from openviking.utils.config.open_viking_config import initialize_openviking_config
from openviking.utils.config.storage_config import StorageConfig
from openviking.utils.resource_processor import ResourceProcessor
from openviking.utils.skill_processor import SkillProcessor
from openviking.utils.uri import VikingURI

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

        # Initialize config
        config = initialize_openviking_config(
            config=config,
            user=user,
            path=path,
            vectordb_url=vectordb_url,
            agfs_url=agfs_url,
        )
        self._config = config
        self.user = config.user

        self._agfs_manager = None
        self._agfs_url = None
        self._vikingdb_manager = self.__init_storage(config.storage)

        # Get embedder instance - must succeed for proper initialization
        self._embedder: Optional[Any] = None
        self._embedder = config.embedding.get_embedder()
        logger.info(
            f"Initialized embedder (dim {config.embedding.dimension}, sparse {self._embedder.is_sparse})"
        )

        # Initialize VikingFS (will be created in _ensure_initialized)
        self._viking_fs: Optional["VikingFS"] = None

        # Initialize coordinated writer
        self._resource_processor: Optional[ResourceProcessor] = None

        # Initialize compressor
        self._session_compressor: Optional["SessionCompressor"] = None

        # Initialize collections flag
        self._initialized = False

        # Mark singleton as initialized
        self._singleton_initialized = True

    @property
    def viking_fs(self) -> "VikingFS":
        return self._viking_fs

    def __init_storage(self, config: StorageConfig) -> VikingDBManager:
        """Initialize storage resources."""
        if config.agfs.backend == "local":
            # Initialize AGFS manager (auto-start subprocess)
            self._agfs_manager = AGFSManager(config=config.agfs)
            self._agfs_manager.start()
            self._agfs_url = self._agfs_manager.url
        else:
            self._agfs_url = config.agfs.url

        self._vikingdb_manager = VikingDBManager(
            vectordb_config=config.vectordb, agfs_config=config.agfs
        )
        return self._vikingdb_manager

    async def close(self) -> None:
        """Close OpenViking and release resources."""
        if self._agfs_manager:
            self._agfs_manager.stop()
            self._agfs_manager = None

        if self._vikingdb_manager:
            await self._vikingdb_manager.close()
            self._vikingdb_manager = None

        self._viking_fs = None
        self._resource_processor = None
        self._skill_processor = None
        self._session_compressor = None
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

    async def initialize(self) -> None:
        """Initialize OpenViking storage and indexes."""
        await self._ensure_initialized()

    async def _ensure_initialized(self):
        """Ensure storage collections are initialized."""
        if self._vikingdb_manager is None:
            self._vikingdb_manager = self.__init_storage(self._config.storage)

        if self._embedder is None:
            self._embedder = self._config.embedding.get_embedder()

        if self._initialized:
            logger.debug(
                f"Already initialized, _session_compressor={bool(self._session_compressor)}"
            )
            return

        assert self._vikingdb_manager is not None, "VikingDB manager must be initialized"
        config = get_openviking_config()

        # Create single context collection
        await init_context_collection(self._vikingdb_manager)
        # Initialize VikingFS (singleton)
        self._viking_fs = init_viking_fs(
            agfs_url=self._agfs_url or "http://localhost:8080",
            query_embedder=self._embedder,
            rerank_config=config.rerank,
            vector_store=self._vikingdb_manager,
            timeout=config.storage.agfs.timeout,
        )

        # Initialize user directories if user is provided
        directory_initializer = DirectoryInitializer(vikingdb=self._vikingdb_manager)
        await directory_initializer.initialize_all()

        # Initialize user directories
        count = await directory_initializer.initialize_user_directories()
        logger.info(f"Initialized {count} directories for user scope")

        # Initialize processors
        self._resource_processor = ResourceProcessor(vikingdb=self._vikingdb_manager)
        self._skill_processor = SkillProcessor(vikingdb=self._vikingdb_manager)
        self._session_compressor = SessionCompressor(vikingdb=self._vikingdb_manager)

        # Mark collections as initialized
        self._initialized = True

    def session(self, session_id: Optional[str] = None) -> Session:
        """
        Create a new session or load an existing one.

        Args:
            session_id: Session ID, creates a new session (auto-generated ID) if None
        """
        return Session(
            client=self,
            user=self.user,
            session_id=session_id,
        )

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

        if not self._resource_processor:
            raise RuntimeError("CoordinatedWriter not initialized")

        # add_resource only supports resources scope
        if target and target.startswith("viking://"):
            parsed = VikingURI(target)
            if parsed.scope != "resources":
                raise ValueError(
                    f"add_resource only supports resources scope, use dedicated interface to add {parsed.scope} content"
                )

        result = await self._resource_processor.process_resource(
            path=path,
            reason=reason,
            instruction=instruction,
            scope="resources",
            target=target,
        )

        if wait:
            from openviking.storage.queuefs import get_queue_manager

            qm = get_queue_manager()
            status = await qm.wait_complete(timeout=timeout)
            result["queue_status"] = {
                name: {
                    "processed": s.processed,
                    "error_count": s.error_count,
                    "errors": [{"message": e.message} for e in s.errors],
                }
                for name, s in status.items()
            }

        return result

    async def wait_processed(self, timeout: float = None) -> Dict[str, Any]:
        from openviking.storage.queuefs import get_queue_manager

        qm = get_queue_manager()
        status = await qm.wait_complete(timeout=timeout)
        status = {
            name: {
                "processed": s.processed,
                "error_count": s.error_count,
                "errors": [{"message": e.message} for e in s.errors],
            }
            for name, s in status.items()
        }
        return status

    @property
    def observers(self):
        queue_manager = get_queue_manager()
        queue_observer = QueueObserver(queue_manager)
        vikingdb_observer = VikingDBObserver(self._vikingdb_manager)
        config = get_openviking_config()
        vlm_observer = VLMObserver(config.vlm.get_vlm_instance())

        available_observers = {
            "queue": queue_observer,
            "vikingdb": vikingdb_observer,
            "vlm": vlm_observer,
        }
        result = {}
        for name in available_observers.keys():
            observer_name = name.lower()
            if observer_name not in available_observers:
                raise ValueError(
                    f"Unknown observer: {observer_name}. "
                    f"Available observers: {list(available_observers.keys())}"
                )
            result[observer_name] = available_observers[observer_name]
        return result

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

        if not self.viking_fs:
            raise RuntimeError("VikingFS not initialized")

        result = await self._skill_processor.process_skill(
            data=data,
            viking_fs=self.viking_fs,
            user=self.user,
        )

        if wait:
            from openviking.storage.queuefs import get_queue_manager

            qm = get_queue_manager()
            status = await qm.wait_complete(timeout=timeout)
            result["queue_status"] = {
                name: {
                    "processed": s.processed,
                    "error_count": s.error_count,
                    "errors": [{"message": e.message} for e in s.errors],
                }
                for name, s in status.items()
            }

        return result

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
            grep: Keyword filters
            filter: Metadata filters

        Returns:
            FindResult
        """
        await self._ensure_initialized()

        session_info = None
        if session:
            session_info = session.get_context_for_search(query)

        if not self._viking_fs:
            raise RuntimeError("VikingFS not initialized")

        return await self._viking_fs.search(
            query=query,
            target_uri=target_uri,
            session_info=session_info,
            limit=limit,
            score_threshold=score_threshold,
            filter=filter,
        )

    # ============= VikingFS specific capabilities =============

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
        if not self._viking_fs:
            raise RuntimeError("VikingFS not initialized")
        return await self._viking_fs.find(
            query=query,
            target_uri=target_uri,
            limit=limit,
            score_threshold=score_threshold,
            filter=filter,
        )

    async def abstract(self, uri: str) -> str:
        """Read L0 abstract (.abstract.md)"""
        await self._ensure_initialized()
        if not self._viking_fs:
            raise RuntimeError("VikingFS not initialized")
        return await self._viking_fs.abstract(uri)

    async def overview(self, uri: str) -> str:
        """Read L1 overview (.overview.md)"""
        await self._ensure_initialized()
        if not self._viking_fs:
            raise RuntimeError("VikingFS not initialized")
        return await self._viking_fs.overview(uri)

    async def read(self, uri: str) -> str:
        """Read file content"""
        await self._ensure_initialized()
        if not self._viking_fs:
            raise RuntimeError("VikingFS not initialized")
        return await self._viking_fs.read_file(uri)

    async def relations(self, uri: str) -> List[Dict[str, Any]]:
        """Get relations (returns [{"uri": "...", "reason": "..."}, ...])"""
        await self._ensure_initialized()
        if not self._viking_fs:
            raise RuntimeError("VikingFS not initialized")
        return await self._viking_fs.relations(uri)

    async def ls(self, uri: str, **kwargs) -> List[Any]:
        """
        List directory contents.

        Args:
            uri: Viking URI
            simple: Return only relative path list (bool, default: False)
            recursive: List all subdirectories recursively (bool, default: False)
        """
        await self._ensure_initialized()
        if not self._viking_fs:
            raise RuntimeError("VikingFS not initialized")

        recursive = kwargs.get("recursive", False)
        simple = kwargs.get("simple", False)

        if recursive:
            entries = await self._viking_fs.tree(uri)
        else:
            entries = await self._viking_fs.ls(uri)

        if simple:
            return [e.get("rel_path", e.get("name", "")) for e in entries]
        else:
            return entries

    async def link(self, from_uri: str, uris: Any, reason: str = "") -> None:
        """
        Create link (single or multiple).

        Args:
            from_uri: Source URI
            uris: Target URI or list of URIs
            reason: Reason for linking
        """
        await self._ensure_initialized()
        if not self._viking_fs:
            raise RuntimeError("VikingFS not initialized")
        await self.viking_fs.link(from_uri, uris, reason)

    async def unlink(self, from_uri: str, uri: str) -> None:
        """
        Remove link (remove specified URI from uris).

        Args:
            from_uri: Source URI
            uri: Target URI to remove
        """
        await self._ensure_initialized()
        if not self._viking_fs:
            raise RuntimeError("VikingFS not initialized")
        await self._viking_fs.unlink(from_uri, uri)

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
        return await local_export_ovpack(self._viking_fs, uri, to)

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
        return await local_import_ovpack(
            self._viking_fs, file_path, parent, force=force, vectorize=vectorize
        )

    async def rm(self, uri: str, recursive: bool = False) -> None:
        """Remove resource"""
        await self._ensure_initialized()
        if not self._viking_fs:
            raise RuntimeError("VikingFS not initialized")
        await self._viking_fs.rm(uri, recursive=recursive)

    async def grep(self, uri: str, pattern: str, case_insensitive: bool = False) -> Dict:
        """Content search"""
        await self._ensure_initialized()
        if not self._viking_fs:
            raise RuntimeError("VikingFS not initialized")
        return await self._viking_fs.grep(uri, pattern, case_insensitive=case_insensitive)

    async def glob(self, pattern: str, uri: str = "viking://") -> Dict:
        """File pattern matching"""
        await self._ensure_initialized()
        if not self._viking_fs:
            raise RuntimeError("VikingFS not initialized")
        return await self._viking_fs.glob(pattern, uri=uri)

    async def mv(self, from_uri: str, to_uri: str) -> None:
        """Move resource"""
        await self._ensure_initialized()
        if not self._viking_fs:
            raise RuntimeError("VikingFS not initialized")
        await self._viking_fs.mv(from_uri, to_uri)

    async def tree(self, uri: str) -> Dict:
        """Get directory tree"""
        await self._ensure_initialized()
        if not self._viking_fs:
            raise RuntimeError("VikingFS not initialized")
        return await self._viking_fs.tree(uri)

    async def mkdir(self, uri: str) -> None:
        """Create directory"""
        await self._ensure_initialized()
        if not self._viking_fs:
            raise RuntimeError("VikingFS not initialized")
        await self._viking_fs.mkdir(uri)

    async def stat(self, uri: str) -> Dict:
        """Get resource status"""
        await self._ensure_initialized()
        if not self._viking_fs:
            raise RuntimeError("VikingFS not initialized")
        return await self._viking_fs.stat(uri)
