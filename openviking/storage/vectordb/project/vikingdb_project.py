# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
from typing import Any, Dict, List, Optional

from openviking.storage.vectordb.collection.collection import (
    Collection,
    load_collection_class,
)
from openviking.storage.vectordb.collection.vikingdb_clients import (
    VIKINGDB_APIS,
    VikingDBClient,
)
from openviking_cli.utils.logger import default_logger as logger


def get_or_create_vikingdb_project(
    project_name: str = "default", config: Optional[Dict[str, Any]] = None
):
    """
    Get or create a VikingDB project for private deployment.

    Args:
        project_name: Project name
        config: Configuration dict with keys:
            - Host: VikingDB service host
            - Headers: Custom headers for authentication/context
            - CollectionClass: Class path for collection implementation
            - CollectionArgs: Optional dictionary of arguments to pass to collection constructor

    Returns:
        VikingDBProject instance
    """
    if config is None:
        raise ValueError("config is required")

    host = config.get("Host")
    headers = config.get("Headers")
    collection_class_path = config.get(
        "CollectionClass",
        "openviking.storage.vectordb.collection.vikingdb_collection.VikingDBCollection",
    )
    # Extract any other arguments that might be needed for collection initialization
    collection_args = config.get("CollectionArgs", {})

    if not host:
        raise ValueError("config must contain 'Host'")

    return VikingDBProject(
        host=host,
        headers=headers,
        project_name=project_name,
        collection_class_path=collection_class_path,
        collection_args=collection_args,
    )


class VikingDBProject:
    """
    VikingDB project class for private deployment.
    Manages multiple VikingDBCollection instances.
    """

    def __init__(
        self,
        host: str,
        headers: Optional[Dict[str, str]] = None,
        project_name: str = "default",
        collection_class_path: str = "openviking.storage.vectordb.collection.vikingdb_collection.VikingDBCollection",
        collection_args: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize VikingDB project.

        Args:
            host: VikingDB service host
            headers: Custom headers for requests
            project_name: Project name
            collection_class_path: Python path to the collection class
            collection_args: Optional dictionary of arguments to pass to collection constructor
        """
        self.host = host
        self.headers = headers
        self.project_name = project_name
        self.collection_class_path = collection_class_path
        self.CollectionClass = load_collection_class(self.collection_class_path)
        self.collection_args = collection_args or {}

        logger.info(
            f"Initialized VikingDB project: {project_name} with host {host} and collection class {collection_class_path}"
        )

    def close(self):
        """Close project"""
        pass

    def has_collection(self, collection_name: str) -> bool:
        """Check if collection exists by calling API"""
        client = VikingDBClient(self.host, self.headers)
        path, method = VIKINGDB_APIS["GetVikingdbCollection"]
        data = {"ProjectName": self.project_name, "CollectionName": collection_name}
        response = client.do_req(method, path=path, req_body=data)
        return response.status_code == 200

    def get_collection(self, collection_name: str) -> Optional[Collection]:
        """Get collection by name by calling API"""
        client = VikingDBClient(self.host, self.headers)
        path, method = VIKINGDB_APIS["GetVikingdbCollection"]
        data = {"ProjectName": self.project_name, "CollectionName": collection_name}
        response = client.do_req(method, path=path, req_body=data)
        if response.status_code != 200:
            return None

        try:
            result = response.json()
            meta_data = result.get("Result", {})
            if not meta_data:
                return None
            # Prepare arguments for collection constructor
            # Default arguments
            kwargs = {
                "host": self.host,
                "headers": self.headers,
                "meta_data": meta_data,
            }
            # Update with user-provided arguments (can override defaults if needed, though usually additive)
            kwargs.update(self.collection_args)
            
            vikingdb_collection = self.CollectionClass(**kwargs)
            return Collection(vikingdb_collection)
        except Exception:
            return None

    def _get_collections(self) -> List[str]:
        """List all collection names from server"""
        client = VikingDBClient(self.host, self.headers)
        path, method = VIKINGDB_APIS["ListVikingdbCollection"]
        data = {"ProjectName": self.project_name}
        response = client.do_req(method, path=path, req_body=data)
        if response.status_code != 200:
            logger.error(f"List collections failed: {response.text}")
            return []
        try:
            result = response.json()
            colls = result.get("Result", {}).get("Collections", [])
            return colls
        except Exception:
            return []

    def list_collections(self) -> List[str]:
        """List all collection names from server"""
        colls = self._get_collections()
        return [coll.get("CollectionName") for coll in colls]

    def get_collections(self) -> Dict[str, Collection]:
        """Get all collections from server"""
        colls = self._get_collections()
        
        # Prepare base arguments
        base_kwargs = {
            "host": self.host,
            "headers": self.headers,
        }
        
        collections = {}
        for c in colls:
            kwargs = base_kwargs.copy()
            kwargs["meta_data"] = c
            kwargs.update(self.collection_args)
            
            collections[c["CollectionName"]] = Collection(
                self.CollectionClass(**kwargs)
            )
            
        return collections

    def create_collection(self, collection_name: str, meta_data: Dict[str, Any]) -> Collection:
        """collection should be pre-created"""
        raise NotImplementedError("collection should be pre-created")

    def get_or_create_collection(
        self, collection_name: str, meta_data: Optional[Dict[str, Any]] = None
    ) -> Collection:
        """
        Get or create collection.

        Args:
            collection_name: Collection name
            meta_data: Collection metadata (required if not exists)

        Returns:
            Collection instance
        """
        collection = self.get_collection(collection_name)
        if collection:
            return collection

        if meta_data is None:
            raise ValueError(f"meta_data is required to create collection {collection_name}")

        return self.create_collection(collection_name, meta_data)

    def drop_collection(self, collection_name: str):
        """Drop specified collection"""
        collection = self.get_collection(collection_name)
        if not collection:
            logger.warning(f"Collection {collection_name} does not exist")
            return

        collection.drop()
        logger.info(f"Dropped VikingDB collection: {collection_name}")
