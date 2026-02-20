# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Qdrant project implementation for OpenViking.

Provides project-level management for Qdrant collections with support for
hybrid search (dense + sparse vectors) and RRF fusion.
"""

from typing import Any, Dict, Optional

from qdrant_client import QdrantClient

from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.qdrant_collection import (
    QdrantCollection,
    get_or_create_qdrant_collection,
)
from openviking.storage.vectordb.project.project import IProject, Project
from openviking.storage.vectordb.utils.dict_utils import ThreadSafeDictManager
from openviking.utils import get_logger

logger = get_logger(__name__)


def get_or_create_qdrant_project(
    url: str = "http://localhost:6333",
    api_key: Optional[str] = None,
    grpc_port: Optional[int] = None,
    prefer_grpc: bool = False,
    timeout: Optional[float] = None,
    project_name: str = "default",
    vector_dim: int = 0,
    distance_metric: str = "cosine",
) -> Project:
    """Get or create a Qdrant project.

    Args:
        url: Qdrant server URL
        api_key: API key for authentication
        grpc_port: gRPC port for faster operations
        prefer_grpc: Whether to prefer gRPC over HTTP
        timeout: Connection timeout in seconds
        project_name: Name of the project (used as prefix for collections)
        vector_dim: Default vector dimension for collections
        distance_metric: Default distance metric for similarity search

    Returns:
        Project instance wrapping QdrantProject
    """
    project = QdrantProject(
        url=url,
        api_key=api_key,
        grpc_port=grpc_port,
        prefer_grpc=prefer_grpc,
        timeout=timeout,
        project_name=project_name,
        vector_dim=vector_dim,
        distance_metric=distance_metric,
    )
    return Project(project)


class QdrantProject(IProject):
    """Qdrant project implementation.

    Manages multiple Qdrant collections under a project namespace.
    Supports automatic collection discovery and creation.
    """

    def __init__(
        self,
        url: str = "http://localhost:6333",
        api_key: Optional[str] = None,
        grpc_port: Optional[int] = None,
        prefer_grpc: bool = False,
        timeout: Optional[float] = None,
        project_name: str = "default",
        vector_dim: int = 0,
        distance_metric: str = "cosine",
    ):
        """Initialize Qdrant project.

        Args:
            url: Qdrant server URL
            api_key: API key for authentication
            grpc_port: gRPC port for faster operations
            prefer_grpc: Whether to prefer gRPC over HTTP
            timeout: Connection timeout in seconds
            project_name: Name of the project
            vector_dim: Default vector dimension
            distance_metric: Default distance metric
        """
        super().__init__(project_name)

        self.url = url
        self.api_key = api_key
        self.vector_dim = vector_dim
        self.distance_metric = distance_metric

        # Initialize Qdrant client
        client_kwargs = {"url": url}
        if api_key:
            client_kwargs["api_key"] = api_key
        if grpc_port:
            client_kwargs["grpc_port"] = grpc_port
        if prefer_grpc:
            client_kwargs["prefer_grpc"] = prefer_grpc
        if timeout:
            client_kwargs["timeout"] = timeout

        self.client = QdrantClient(**client_kwargs)

        # Collection cache
        self.collections = ThreadSafeDictManager[Collection]()

        # Load existing collections
        self._load_existing_collections()

        logger.info(f"Qdrant project initialized: {url}, project={project_name}")

    def _load_existing_collections(self):
        """Load existing collections from Qdrant server."""
        try:
            collections_info = self.client.get_collections()

            for collection_info in collections_info.collections:
                collection_name = collection_info.name

                # Skip if not belonging to this project (if using prefixes)
                # For now, we load all collections

                try:
                    # Get collection details to determine vector dimension
                    info = self.client.get_collection(collection_name)

                    # Extract vector dimension from config
                    vector_dim = self.vector_dim
                    if info.config and info.config.params:
                        vectors_config = info.config.params.vectors
                        if isinstance(vectors_config, dict):
                            # Named vectors
                            if "dense" in vectors_config:
                                vector_dim = vectors_config["dense"].size
                        elif hasattr(vectors_config, "size"):
                            vector_dim = vectors_config.size

                    # Create collection wrapper
                    collection = get_or_create_qdrant_collection(
                        client=self.client,
                        collection_name=collection_name,
                        vector_dim=vector_dim,
                        distance_metric=self.distance_metric,
                    )
                    self.collections.set(collection_name, collection)

                    logger.info(f"Loaded Qdrant collection: {collection_name}")

                except Exception as e:
                    logger.warning(f"Failed to load collection {collection_name}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Failed to load existing collections: {e}")

    def close(self):
        """Close the project and release resources."""
        try:
            # Close all collections
            for name in self.collections.list_names():
                try:
                    collection = self.collections.get(name)
                    if collection:
                        collection.close()
                except Exception as e:
                    logger.warning(f"Error closing collection {name}: {e}")

            self.collections.clear()

            # Close Qdrant client
            if hasattr(self.client, 'close'):
                self.client.close()

            logger.info("Qdrant project closed")

        except Exception as e:
            logger.error(f"Error closing Qdrant project: {e}")

    def has_collection(self, collection_name: str) -> bool:
        """Check if a collection exists."""
        # Check local cache first
        if self.collections.has(collection_name):
            return True

        # Check Qdrant server
        try:
            collections_info = self.client.get_collections()
            for info in collections_info.collections:
                if info.name == collection_name:
                    return True
            return False
        except Exception as e:
            logger.error(f"Error checking collection existence: {e}")
            return False

    def get_collection(self, collection_name: str) -> Optional[Collection]:
        """Retrieve a collection by name."""
        # Check cache first
        collection = self.collections.get(collection_name)
        if collection:
            return collection

        # Try to load from Qdrant
        if self.has_collection(collection_name):
            try:
                info = self.client.get_collection(collection_name)

                # Extract vector dimension
                vector_dim = self.vector_dim
                if info.config and info.config.params:
                    vectors_config = info.config.params.vectors
                    if isinstance(vectors_config, dict) and "dense" in vectors_config:
                        vector_dim = vectors_config["dense"].size
                    elif hasattr(vectors_config, "size"):
                        vector_dim = vectors_config.size

                collection = get_or_create_qdrant_collection(
                    client=self.client,
                    collection_name=collection_name,
                    vector_dim=vector_dim,
                    distance_metric=self.distance_metric,
                )
                self.collections.set(collection_name, collection)
                return collection

            except Exception as e:
                logger.error(f"Error loading collection {collection_name}: {e}")
                return None

        return None

    def get_collections(self) -> Dict[str, Collection]:
        """Get all collections in the project."""
        return self.collections.get_all()

    def list_collections(self) -> list:
        """List all collection names."""
        try:
            collections_info = self.client.get_collections()
            return [info.name for info in collections_info.collections]
        except Exception as e:
            logger.error(f"Error listing collections: {e}")
            return self.collections.list_names()

    def create_collection(
        self,
        collection_name: str,
        collection_meta: Dict[str, Any],
    ) -> Collection:
        """Create a new collection.

        Args:
            collection_name: Name for the new collection
            collection_meta: Collection metadata including Fields definition

        Returns:
            The newly created Collection instance
        """
        try:
            # Check if already exists
            if self.has_collection(collection_name):
                logger.warning(f"Collection {collection_name} already exists, returning existing")
                return self.get_collection(collection_name)

            # Extract vector dimension from metadata
            vector_dim = self.vector_dim
            for field in collection_meta.get("Fields", []):
                if field.get("FieldType") == "vector":
                    vector_dim = field.get("Dim", self.vector_dim)
                    break

            # Create collection
            collection = get_or_create_qdrant_collection(
                client=self.client,
                collection_name=collection_name,
                meta_data=collection_meta,
                vector_dim=vector_dim,
                distance_metric=self.distance_metric,
            )

            # Cache it
            self.collections.set(collection_name, collection)

            logger.info(f"Created Qdrant collection: {collection_name} (dim={vector_dim})")
            return collection

        except Exception as e:
            logger.error(f"Error creating collection {collection_name}: {e}")
            raise

    def drop_collection(self, collection_name: str):
        """Delete a collection."""
        try:
            # Remove from cache
            collection = self.collections.get(collection_name)
            if collection:
                collection.close()
                self.collections.remove(collection_name)

            # Delete from Qdrant
            self.client.delete_collection(collection_name)

            logger.info(f"Dropped Qdrant collection: {collection_name}")

        except Exception as e:
            logger.error(f"Error dropping collection {collection_name}: {e}")
            raise
