# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import importlib
import logging
from typing import Any, Dict, Optional

from openviking.storage.vectordb.project.project import IProject

logger = logging.getLogger(__name__)


class VikingProject(IProject):
    """
    Generic implementation of IProject using reflection to avoid direct dependency
    on any specific viking client implementation.
    """

    def __init__(self, project_name: str = "default", config: Optional[Dict[str, Any]] = None):
        """
        Initialize VikingProject with configuration.

        Args:
            project_name: The name of the project.
            config: Configuration dictionary containing:
                - collection_package_name: The package name for collection implementation
                - collection_class_name: The class name for collection implementation
                - ... other collection-specific configuration
        """
        super().__init__(project_name)
        self.config = config or {}
        self.collections: Dict[str, Any] = {}
        
        # 1. Dynamic Import for Collection Implementation
        collection_package_name = self.config.get("collection_package_name", "openviking.storage.vectordb.collection.viking_collection")
        collection_class_name = self.config.get("collection_class_name", "VikingCollection")
        
        try:
            self._collection_module = importlib.import_module(collection_package_name)
        except ImportError as e:
            raise ImportError(f"Failed to import collection module '{collection_package_name}': {e}")

        try:
            self._CollectionClass = getattr(self._collection_module, collection_class_name)
        except AttributeError as e:
            raise AttributeError(f"Failed to get collection class '{collection_class_name}' from '{collection_package_name}': {e}")

    def close(self):
        """Close the project and release all associated resources."""
        # Close all collections
        for collection_name, collection in self.collections.items():
            try:
                collection.close()
            except Exception as e:
                logger.warning(f"Failed to close collection '{collection_name}': {e}")
        self.collections.clear()

    def has_collection(self, collection_name: str) -> bool:
        """Check if a collection exists in the project."""
        return collection_name in self.collections

    def get_collection(self, collection_name: str) -> Any:
        """Retrieve a collection by name."""
        return self.collections.get(collection_name)

    def get_collections(self) -> Dict[str, Any]:
        """Get all collections in the project."""
        return self.collections.copy()

    def create_collection(self, collection_name: str, collection_meta: Dict[str, Any]) -> Any:
        """
        Create a new collection in the project.

        Args:
            collection_name: Name for the new collection.
            collection_meta: Metadata configuration for the collection.

        Returns:
            The newly created collection instance.
        """
        if self.has_collection(collection_name):
            raise ValueError(f"Collection '{collection_name}' already exists")

        # Merge project config with collection-specific config
        collection_config = self.config.copy()
        collection_config.update(collection_meta.get("config", {}))
        
        # Create collection instance using reflection
        try:
            collection_instance = self._CollectionClass(
                collection_name=collection_name,
                config=collection_config
            )
        except Exception as e:
            raise RuntimeError(f"Failed to create collection '{collection_name}': {e}")

        self.collections[collection_name] = collection_instance
        return collection_instance

    def drop_collection(self, collection_name: str):
        """Delete a collection from the project."""
        if collection_name not in self.collections:
            raise ValueError(f"Collection '{collection_name}' does not exist")

        collection = self.collections[collection_name]
        try:
            collection.drop()
        except Exception as e:
            logger.warning(f"Failed to drop collection '{collection_name}': {e}")
        finally:
            self.collections.pop(collection_name, None)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()