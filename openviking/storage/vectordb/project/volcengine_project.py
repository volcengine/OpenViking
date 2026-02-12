# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
from typing import Any, Dict, Optional

from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.volcengine_collection import (
    get_or_create_volcengine_collection,
)
from openviking.storage.vectordb.utils.dict_utils import ThreadSafeDictManager
from openviking_cli.utils.logger import default_logger as logger


def get_or_create_volcengine_project(
    project_name: str = "default", config: Optional[Dict[str, Any]] = None
):
    """
    Get or create a Volcengine project

    Args:
        project_name: Project name
        config: Configuration dict with keys:
            - AK: Volcengine Access Key
            - SK: Volcengine Secret Key
            - Region: Volcengine region (e.g., "cn-beijing")

    Returns:
        VolcengineProject instance
    """
    if config is None:
        raise ValueError("config is required")

    # Extract configuration
    ak = config.get("AK")
    sk = config.get("SK")
    region = config.get("Region")

    if not all([ak, sk, region]):
        raise ValueError("config must contain 'AK', 'SK', and 'Region'")

    project = VolcengineProject(ak=ak, sk=sk, region=region, project_name=project_name)
    return project


class VolcengineProject:
    """
    Volcengine project class that connects to Volcengine VikingDB service and manages multiple Collections

    Supports all operations on Volcengine VikingDB service
    """

    def __init__(self, ak: str, sk: str, region: str, project_name: str = "default"):
        """
        Initialize Volcengine project

        Args:
            ak: Volcengine Access Key
            sk: Volcengine Secret Key
            region: Volcengine region (e.g., "cn-beijing")
            project_name: Project name
        """
        self.ak = ak
        self.sk = sk
        self.region = region
        self.project_name = project_name
        self.collections = ThreadSafeDictManager[Collection]()

        logger.info(f"Initialized Volcengine project: {project_name} in region {region}")

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

        # Prepare config for volcengine collection
        config = {
            "AK": self.ak,
            "SK": self.sk,
            "Region": self.region,
        }

        # Update meta_data with CollectionName if not present
        updated_meta = {
            **meta_data,
            "CollectionName": collection_name,
        }

        logger.info(f"Creating Volcengine collection: {collection_name}")
        collection = get_or_create_volcengine_collection(config=config, meta_data=updated_meta)

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
        if not self.has_collection(collection_name):
            logger.warning(f"Collection {collection_name} does not exist")
            return

        collection = self.get_collection(collection_name)
        if collection:
            collection.close()

        self.collections.remove(collection_name)
        logger.info(f"Dropped Volcengine collection: {collection_name}")

    def get_or_create_collection(
        self, collection_name: str, meta_data: Optional[Dict[str, Any]] = None
    ) -> Collection:
        """
        Get an existing collection or create a new one if it doesn't exist

        Args:
            collection_name: Collection name
            meta_data: Collection metadata (required if collection doesn't exist)

        Returns:
            Collection instance
        """
        if self.has_collection(collection_name):
            return self.get_collection(collection_name)

        if meta_data is None:
            raise ValueError(f"meta_data is required to create collection {collection_name}")

        return self.create_collection(collection_name, meta_data)
