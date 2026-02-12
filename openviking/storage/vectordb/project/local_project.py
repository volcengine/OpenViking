# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import json
import os
from typing import Any, Dict, Optional

from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.local_collection import get_or_create_local_collection
from openviking.storage.vectordb.utils.dict_utils import ThreadSafeDictManager
from openviking_cli.utils.logger import default_logger as logger


def get_or_create_local_project(path: str = ""):
    """Get or create local project.

    Args:
        path: Project path. If empty, creates volatile project; otherwise creates persistent project.

    Returns:
        LocalProject instance
    """
    if not path:
        # Volatile project - not persisted
        project = LocalProject(path="")
        return project
    else:
        # Persistent project - persisted to disk
        os.makedirs(path, exist_ok=True)
        project = LocalProject(path=path)
        return project


class LocalProject:
    """Local project class, manages multiple Collections.

    Supports two modes:
    1. Volatile mode (path=""): collections stored in memory, not persisted
    2. Persistent mode (path!=""): collections persisted to disk
    """

    def __init__(self, path: str = ""):
        """Initialize local project.

        Args:
            path: Project path
                - If empty: creates volatile project, collections not persisted
                - If not empty: creates persistent project, auto-loads all existing collections in that directory
        """
        self.path = path
        self.collections = ThreadSafeDictManager[Collection]()

        # If persistent project, load existing collections
        if self.path:
            self._load_existing_collections()

    def _load_existing_collections(self):
        """Load existing collections from disk.

        Scans all subdirectories under path, each subdirectory is treated as a collection.
        """
        if not os.path.exists(self.path):
            logger.info(f"Project path does not exist: {self.path}")
            return

        # Scan all subdirectories under path
        try:
            entries = os.listdir(self.path)
        except Exception as e:
            logger.error(f"Failed to list directory {self.path}: {e}")
            return

        for entry in entries:
            entry_path = os.path.join(self.path, entry)

            # Only process directories
            if not os.path.isdir(entry_path):
                continue

            # Check if it's a collection directory (should contain collection_meta.json)
            meta_path = os.path.join(entry_path, "collection_meta.json")
            if not os.path.exists(meta_path):
                logger.warning(f"Directory {entry} does not contain collection_meta.json, skipping")
                continue

            # Read collection metadata
            try:
                with open(meta_path, "r") as f:
                    meta_data = json.load(f)

                collection_name = meta_data.get("CollectionName", entry)

                # Load collection
                logger.info(f"Loading collection: {collection_name} from {entry_path}")
                collection = get_or_create_local_collection(path=entry_path)
                self.collections.set(collection_name, collection)

                logger.info(f"Successfully loaded collection: {collection_name}")
            except Exception as e:
                logger.error(f"Failed to load collection from {entry_path}: {e}")
                continue

    def close(self):
        """Close project, release all collection resources."""

        def close_collection(name, collection):
            collection.close()

        self.collections.iterate(close_collection)
        self.collections.clear()

    def has_collection(self, collection_name: str) -> bool:
        """Check if collection exists.

        Args:
            collection_name: Collection name

        Returns:
            True if exists, otherwise False
        """
        return self.collections.has(collection_name)

    def get_collection(self, collection_name: str) -> Optional[Collection]:
        """Get collection by name.

        Args:
            collection_name: Collection name

        Returns:
            Collection instance, or None if not exists
        """
        return self.collections.get(collection_name)

    def list_collections(self):
        """List all collection names.

        Returns:
            Collection name list
        """
        return self.collections.list_names()

    def get_collections(self) -> Dict[str, Collection]:
        """Get all collections.

        Returns:
            Dictionary of collection_name -> Collection
        """
        return self.collections.get_all()

    def create_collection(self, collection_name: str, meta_data: Dict[str, Any]) -> Collection:
        """Create new collection.

        Args:
            collection_name: Collection name
            meta_data: Collection metadata, must contain Fields and other configuration

        Returns:
            Newly created Collection instance

        Raises:
            ValueError: If collection already exists
        """
        if self.has_collection(collection_name):
            raise ValueError(f"Collection {collection_name} already exists")

        # Ensure meta_data has CollectionName
        meta_data["CollectionName"] = collection_name

        # Decide whether to create volatile or persistent collection based on project path
        if self.path:
            # Persistent collection
            collection_path = os.path.join(self.path, collection_name)
            os.makedirs(collection_path, exist_ok=True)
            logger.info(f"Creating persistent collection: {collection_name} at {collection_path}")
            collection = get_or_create_local_collection(meta_data=meta_data, path=collection_path)
        else:
            # Volatile collection
            logger.info(f"Creating volatile collection: {collection_name}")
            collection = get_or_create_local_collection(meta_data=meta_data, path="")

        self.collections.set(collection_name, collection)
        return collection

    def add_collection(self, collection_name: str, collection: Collection) -> Collection:
        """Add existing collection to project.

        Args:
            collection_name: Collection name
            collection: Collection instance

        Returns:
            Added Collection instance
        """
        self.collections.set(collection_name, collection)
        return collection

    def drop_collection(self, collection_name: str):
        """Drop specified collection.

        Args:
            collection_name: Collection name
        """
        collection = self.collections.remove(collection_name)
        if collection:
            collection.drop()
            logger.info(f"Dropped collection: {collection_name}")

    def get_or_create_collection(
        self, collection_name: str, meta_data: Optional[Dict[str, Any]] = None
    ) -> Collection:
        """Get or create collection.

        Args:
            collection_name: Collection name
            meta_data: Collection metadata (only required when creating)

        Returns:
            Collection instance

        Raises:
            ValueError: If collection does not exist and no meta_data provided
        """
        collection = self.get_collection(collection_name)
        if collection:
            return collection

        if meta_data is None:
            raise ValueError(
                f"Collection {collection_name} does not exist and no meta_data provided"
            )

        return self.create_collection(collection_name, meta_data)
