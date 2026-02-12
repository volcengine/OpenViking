# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
from typing import Any, Dict, Optional

from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.http_collection import (
    HttpCollection,
    get_or_create_http_collection,
    list_vikingdb_collections,
)
from openviking.storage.vectordb.utils.dict_utils import ThreadSafeDictManager
from openviking_cli.utils.logger import default_logger as logger


def get_or_create_http_project(
    host: str = "127.0.0.1", port: int = 5000, project_name: str = "default"
):
    """
    Get or create an HTTP project

    Args:
        host: VikingVectorIndex service host address
        port: VikingVectorIndex service port
        project_name: Project name

    Returns:
        HttpProject instance
    """
    project = HttpProject(host=host, port=port, project_name=project_name)
    return project


class HttpProject:
    """
    HTTP project class that connects to remote VikingVectorIndex service via HTTP and manages multiple Collections

    Supports all operations on remote VikingVectorIndex service
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 5000, project_name: str = "default"):
        """
        Initialize HTTP project

        Args:
            host: VikingVectorIndex service host address
            port: VikingVectorIndex service port
            project_name: Project name
        """
        self.host = host
        self.port = port
        self.project_name = project_name
        self.collections = ThreadSafeDictManager[Collection]()

        # Load existing collections from remote service
        self._load_existing_collections()

    def _load_existing_collections(self):
        """
        Load existing collections from remote service
        """
        try:
            # Get remote collections list
            collections_data = list_vikingdb_collections(
                host=self.host, port=self.port, project_name=self.project_name
            )

            if not collections_data:
                logger.info(f"No collections found in remote project: {self.project_name}")
                return

            # Create proxy objects for each collection
            for collection_name in collections_data:
                try:
                    logger.info(f"Loading remote collection: {collection_name}")

                    # Create HTTP collection directly
                    meta_data = {
                        "ProjectName": self.project_name,
                        "CollectionName": collection_name,
                    }

                    http_collection = HttpCollection(
                        ip=self.host, port=self.port, meta_data=meta_data
                    )

                    # Wrap in Collection interface
                    collection = Collection(http_collection)
                    self.collections.set(collection_name, collection)
                    logger.info(f"Successfully loaded remote collection: {collection_name}")
                except Exception as e:
                    logger.error(f"Failed to load remote collection {collection_name}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Failed to load collections from remote server: {e}")

    def close(self):
        """Close project and release all collection resources"""

        def close_collection(name, collection):
            collection.close()

        self.collections.iterate(close_collection)
        self.collections.clear()

    def has_collection(self, collection_name: str) -> bool:
        """
        Check if collection exists

        Args:
            collection_name: Collection name

        Returns:
            True if exists, False otherwise
        """
        return self.collections.has(collection_name)

    def get_collection(self, collection_name: str) -> Optional[Collection]:
        """
        Get collection by name

        Args:
            collection_name: Collection name

        Returns:
            Collection instance, or None if not exists
        """
        return self.collections.get(collection_name)

    def list_collections(self):
        """
        List all collection names

        Returns:
            List of collection names
        """
        return self.collections.list_names()

    def get_collections(self) -> Dict[str, Collection]:
        """
        Get all collections

        Returns:
            Dictionary mapping collection_name -> Collection
        """
        return self.collections.get_all()

    def create_collection(self, collection_name: str, meta_data: Dict[str, Any]) -> Collection:
        """
        Create a new collection

        Args:
            collection_name: Collection name
            meta_data: Collection metadata, must include Fields and other configurations

        Returns:
            Newly created Collection instance

        Raises:
            ValueError: If collection already exists
        """
        if self.has_collection(collection_name):
            logger.warning(
                f"Collection {collection_name} already exists, returning existing collection"
            )
            return self.get_collection(collection_name)

        # Create a new dict with required fields without modifying the input dict
        updated_meta = {
            **meta_data,
            "CollectionName": collection_name,
            "ProjectName": self.project_name,
        }

        logger.info(f"Creating remote collection: {collection_name}")
        collection = get_or_create_http_collection(
            host=self.host, port=self.port, meta_data=updated_meta
        )

        self.collections.set(collection_name, collection)
        return collection

    def add_collection(self, collection_name: str, collection: Collection) -> Collection:
        """
        Add an existing collection to project

        Args:
            collection_name: Collection name
            collection: Collection instance

        Returns:
            Added Collection instance
        """
        self.collections.set(collection_name, collection)
        return collection

    def drop_collection(self, collection_name: str):
        """
        Drop specified collection

        Args:
            collection_name: Collection name
        """
        collection = self.collections.remove(collection_name)
        if collection:
            collection.drop()
            logger.info(f"Dropped remote collection: {collection_name}")

    def get_or_create_collection(
        self, collection_name: str, meta_data: Optional[Dict[str, Any]] = None
    ) -> Collection:
        """
        Get or create collection

        Args:
            collection_name: Collection name
            meta_data: Collection metadata (required only when creating)

        Returns:
            Collection instance

        Raises:
            ValueError: If collection does not exist and no meta_data provided
        """
        if self.has_collection(collection_name):
            return self.get_collection(collection_name)

        if meta_data is None:
            raise ValueError(
                f"Collection {collection_name} does not exist and no meta_data provided"
            )

        return self.create_collection(collection_name, meta_data)
