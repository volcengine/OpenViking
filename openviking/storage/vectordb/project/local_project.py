# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
import json
import os
from typing import Any, Dict, Optional

from openviking.observability.log_safety import safe_path_for_log, short_hash
from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.local_collection import get_or_create_local_collection
from openviking.storage.vectordb.utils.dict_utils import ThreadSafeDictManager
from openviking.storage.vectordb.utils.path_safety import (
    resolve_storage_path,
    safe_join,
    safe_join_name,
)
from openviking.storage.vectordb.utils.validation import validate_name_str
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


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
        project_path = str(resolve_storage_path(path))
        os.makedirs(project_path, exist_ok=True)
        project = LocalProject(path=project_path)
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
        self.path = str(resolve_storage_path(path)) if path else ""
        self.collections = ThreadSafeDictManager[Collection]()

        # If persistent project, load existing collections
        if self.path:
            self._load_existing_collections()

    def _load_existing_collections(self):
        """Load existing collections from disk.

        Scans all subdirectories under path, each subdirectory is treated as a collection.
        """
        if not os.path.exists(self.path):
            logger.info("Project path does not exist: %s", safe_path_for_log(str(self.path)))
            return

        # Scan all subdirectories under path
        try:
            entries = os.listdir(self.path)
        except Exception as e:
            logger.info(
                "Failed to list directory %s: %s",
                safe_path_for_log(str(self.path)),
                type(e).__name__,
            )
            return

        skipped_collections = 0
        for entry in entries:
            try:
                entry_path = safe_join(self.path, entry)
                meta_path = safe_join(entry_path, "collection_meta.json")
            except ValueError as _log_error:
                skipped_collections += 1
                logger.debug(
                    "Skipping invalid collection directory under %s: %s error_type=%s",
                    safe_path_for_log(str(self.path)),
                    safe_path_for_log(str(entry)),
                    type(_log_error).__name__,
                )
                continue

            # Only process directories
            if not entry_path.is_dir():
                continue

            # Check if it's a collection directory (should contain collection_meta.json)
            if not meta_path.exists():
                skipped_collections += 1
                logger.debug(
                    "Directory %s does not contain collection_meta.json, skipping",
                    safe_path_for_log(str(entry)),
                )
                continue

            # Read collection metadata
            try:
                with open(meta_path, "r") as f:
                    meta_data = json.load(f)

                collection_name = meta_data.get("CollectionName", entry)
                validate_name_str(collection_name)

                # Load collection
                logger.debug(
                    "Loading collection: %s from %s",
                    short_hash(str(collection_name)),
                    safe_path_for_log(str(entry_path)),
                )
                collection = get_or_create_local_collection(path=str(entry_path))
                self.collections.set(collection_name, collection)

                logger.debug("Successfully loaded collection: %s", short_hash(str(collection_name)))
            except Exception as e:
                skipped_collections += 1
                logger.debug(
                    "Failed to load collection from %s: %s",
                    safe_path_for_log(str(entry_path)),
                    type(e).__name__,
                )
                continue

        if skipped_collections:
            logger.info(
                "Collection loading incomplete skipped=%d degraded=true", skipped_collections
            )

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
            validate_name_str(collection_name)
            collection_path = safe_join_name(self.path, collection_name)
            os.makedirs(collection_path, exist_ok=True)
            logger.debug(
                "Creating persistent collection: %s at %s",
                short_hash(str(collection_name)),
                safe_path_for_log(str(collection_path)),
            )
            collection = get_or_create_local_collection(
                meta_data=meta_data, path=str(collection_path)
            )
        else:
            # Volatile collection
            logger.debug("Creating volatile collection: %s", short_hash(str(collection_name)))
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
        collection = self.collections.get(collection_name)
        if collection:
            collection.drop()
            self.collections.remove(collection_name)
            logger.debug("Dropped collection: %s", short_hash(str(collection_name)))
