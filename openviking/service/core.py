# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
OpenViking Service Core.

Main service class that composes all sub-services and manages infrastructure lifecycle.
"""

from typing import Any, Optional

from openviking.agfs_manager import AGFSManager
from openviking.core.directories import DirectoryInitializer
from openviking.service.debug_service import DebugService
from openviking.service.fs_service import FSService
from openviking.service.pack_service import PackService
from openviking.service.relation_service import RelationService
from openviking.service.resource_service import ResourceService
from openviking.service.search_service import SearchService
from openviking.service.session_service import SessionService
from openviking.session.compressor import SessionCompressor
from openviking.storage import VikingDBManager
from openviking.storage.collection_schemas import init_context_collection
from openviking.storage.queuefs.queue_manager import QueueManager, init_queue_manager
from openviking.storage.transaction import TransactionManager, init_transaction_manager
from openviking.storage.viking_fs import VikingFS, init_viking_fs
from openviking.utils.resource_processor import ResourceProcessor
from openviking.utils.skill_processor import SkillProcessor
from openviking_cli.exceptions import NotInitializedError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.config.open_viking_config import initialize_openviking_config
from openviking_cli.utils.config.storage_config import StorageConfig

logger = get_logger(__name__)


class OpenVikingService:
    """
    OpenViking main service class.

    Composes all sub-services and manages infrastructure lifecycle.
    """

    def __init__(
        self,
        path: Optional[str] = None,
        user: Optional[UserIdentifier] = None,
    ):
        """Initialize OpenViking service.

        Args:
            path: Local storage path (overrides ov.conf storage path).
            user: Username for session management.
        """
        # Initialize config from ov.conf
        config = initialize_openviking_config(
            user=user,
            path=path,
        )
        self._config = config
        self._user = user or UserIdentifier(
            config.default_account, config.default_user, config.default_agent
        )

        # Infrastructure
        self._agfs_manager: Optional[AGFSManager] = None
        self._agfs_url: Optional[str] = None
        self._queue_manager: Optional[QueueManager] = None
        self._vikingdb_manager: Optional[VikingDBManager] = None
        self._viking_fs: Optional[VikingFS] = None
        self._embedder: Optional[Any] = None
        self._resource_processor: Optional[ResourceProcessor] = None
        self._skill_processor: Optional[SkillProcessor] = None
        self._session_compressor: Optional[SessionCompressor] = None
        self._transaction_manager: Optional[TransactionManager] = None

        # Sub-services
        self._fs_service = FSService()
        self._relation_service = RelationService()
        self._pack_service = PackService()
        self._search_service = SearchService()
        self._resource_service = ResourceService()
        self._session_service = SessionService()
        self._debug_service = DebugService()

        # State
        self._initialized = False

        # Initialize storage
        self._init_storage(config.storage)

        # Initialize embedder
        self._embedder = config.embedding.get_embedder()
        logger.info(
            f"Initialized embedder (dim {config.embedding.dimension}, sparse {self._embedder.is_sparse})"
        )

    def _init_storage(self, config: StorageConfig) -> None:
        """Initialize storage resources."""
        if config.agfs.backend == "local":
            self._agfs_manager = AGFSManager(config=config.agfs)
            self._agfs_manager.start()
            self._agfs_url = self._agfs_manager.url
            config.agfs.url = self._agfs_url
        else:
            self._agfs_url = config.agfs.url

        # Initialize QueueManager
        if self._agfs_url:
            self._queue_manager = init_queue_manager(
                agfs_url=self._agfs_url,
                timeout=config.agfs.timeout,
            )
        else:
            logger.warning("AGFS URL not configured, skipping queue manager initialization")

        # Initialize VikingDBManager with QueueManager
        self._vikingdb_manager = VikingDBManager(
            vectordb_config=config.vectordb, queue_manager=self._queue_manager
        )

        # Configure queues if QueueManager is available
        if self._queue_manager:
            self._queue_manager.setup_standard_queues(self._vikingdb_manager)

        # Initialize TransactionManager
        self._transaction_manager = init_transaction_manager(agfs_config=config.agfs)

    @property
    def viking_fs(self) -> Optional[VikingFS]:
        """Get VikingFS instance."""
        return self._viking_fs

    @property
    def vikingdb_manager(self) -> Optional[VikingDBManager]:
        """Get VikingDBManager instance."""
        return self._vikingdb_manager

    @property
    def transaction_manager(self) -> Optional[TransactionManager]:
        """Get TransactionManager instance."""
        return self._transaction_manager

    @property
    def session_compressor(self) -> Optional[SessionCompressor]:
        """Get SessionCompressor instance."""
        return self._session_compressor

    @property
    def fs(self) -> FSService:
        """Get FSService instance."""
        return self._fs_service

    @property
    def relations(self) -> RelationService:
        """Get RelationService instance."""
        return self._relation_service

    @property
    def pack(self) -> PackService:
        """Get PackService instance."""
        return self._pack_service

    @property
    def search(self) -> SearchService:
        """Get SearchService instance."""
        return self._search_service

    @property
    def user(self) -> UserIdentifier:
        """Get current user identifier."""
        return self._user

    @property
    def resources(self) -> ResourceService:
        """Get ResourceService instance."""
        return self._resource_service

    @property
    def sessions(self) -> SessionService:
        """Get SessionService instance."""
        return self._session_service

    @property
    def debug(self) -> DebugService:
        """Get DebugService instance."""
        return self._debug_service

    async def initialize(self) -> None:
        """Initialize OpenViking storage and indexes."""
        if self._initialized:
            logger.debug("Already initialized")
            return

        if self._vikingdb_manager is None:
            self._init_storage(self._config.storage)

        if self._embedder is None:
            self._embedder = self._config.embedding.get_embedder()

        config = get_openviking_config()

        # Create context collection
        await init_context_collection(self._vikingdb_manager)

        # Initialize VikingFS
        self._viking_fs = init_viking_fs(
            agfs_url=self._agfs_url or "http://localhost:8080",
            query_embedder=self._embedder,
            rerank_config=config.rerank,
            vector_store=self._vikingdb_manager,
            timeout=config.storage.agfs.timeout,
        )

        # Initialize directories
        directory_initializer = DirectoryInitializer(vikingdb=self._vikingdb_manager)
        await directory_initializer.initialize_all()
        count = await directory_initializer.initialize_user_directories()
        logger.info(f"Initialized {count} directories for user scope")

        # Initialize processors
        self._resource_processor = ResourceProcessor(vikingdb=self._vikingdb_manager)
        self._skill_processor = SkillProcessor(vikingdb=self._vikingdb_manager)
        self._session_compressor = SessionCompressor(vikingdb=self._vikingdb_manager)

        # Start TransactionManager if initialized
        if self._transaction_manager:
            await self._transaction_manager.start()
            logger.info("TransactionManager started")

        # Wire up sub-services
        self._fs_service.set_viking_fs(self._viking_fs)
        self._relation_service.set_viking_fs(self._viking_fs)
        self._pack_service.set_viking_fs(self._viking_fs)
        self._search_service.set_viking_fs(self._viking_fs)
        self._resource_service.set_dependencies(
            vikingdb=self._vikingdb_manager,
            viking_fs=self._viking_fs,
            resource_processor=self._resource_processor,
            skill_processor=self._skill_processor,
            user=self.user,
        )
        self._session_service.set_dependencies(
            vikingdb=self._vikingdb_manager,
            viking_fs=self._viking_fs,
            session_compressor=self._session_compressor,
            user=self.user,
        )
        self._debug_service.set_dependencies(
            vikingdb=self._vikingdb_manager,
            config=self._config,
        )

        self._initialized = True
        logger.info("OpenVikingService initialized")

    async def close(self) -> None:
        """Close OpenViking and release resources."""
        if self._transaction_manager:
            self._transaction_manager.stop()
            self._transaction_manager = None

        if self._queue_manager:
            self._queue_manager.stop()
            self._queue_manager = None
            logger.info("Queue manager stopped")

        if self._vikingdb_manager:
            await self._vikingdb_manager.close()
            self._vikingdb_manager = None

        if self._agfs_manager:
            self._agfs_manager.stop()
            self._agfs_manager = None

        self._viking_fs = None
        self._resource_processor = None
        self._skill_processor = None
        self._session_compressor = None
        self._initialized = False

        logger.info("OpenVikingService closed")

    def _ensure_initialized(self) -> None:
        """Ensure service is initialized."""
        if not self._initialized:
            raise NotInitializedError("OpenVikingService")
