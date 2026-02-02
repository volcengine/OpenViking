# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
from abc import ABC, abstractmethod
from typing import Any, Dict


class IProject(ABC):
    """Interface defining the contract for project implementations.

    All project implementations must inherit from this interface and implement
    all abstract methods for managing collections.
    """

    def __init__(self, project_name: str = "default"):
        """Initialize the project interface.

        Args:
            project_name (str): Name of the project. Defaults to 'default'.
        """
        self.project_name = project_name

    @abstractmethod
    def close(self):
        """Close the project and release resources.

        Must be implemented by subclasses to properly clean up resources.
        """
        pass

    @abstractmethod
    def has_collection(self, collection_name: str) -> bool:
        """Check if a collection exists.

        Args:
            collection_name (str): Name of the collection to check.

        Returns:
            bool: True if collection exists, False otherwise.
        """
        pass

    @abstractmethod
    def get_collection(self, collection_name: str) -> Any:
        """Retrieve a collection by name.

        Args:
            collection_name (str): Name of the collection to retrieve.

        Returns:
            Collection: The collection instance, or None if not found.
        """
        pass

    @abstractmethod
    def get_collections(self) -> Dict[str, Any]:
        """Get all collections in the project.

        Returns:
            Dict[str, Collection]: Mapping of collection names to Collection instances.
        """
        pass

    @abstractmethod
    def create_collection(self, collection_name: str, collection_meta: Dict[str, Any]) -> Any:
        """Create a new collection.

        Args:
            collection_name (str): Unique name for the collection.
            collection_meta (Dict[str, Any]): Collection metadata and configuration.

        Returns:
            Collection: The newly created collection instance.
        """
        pass

    @abstractmethod
    def drop_collection(self, collection_name: str):
        """Delete a collection.

        Args:
            collection_name (str): Name of the collection to delete.
        """
        pass


class Project:
    """Wrapper class for managing project operations.

    A Project serves as a container for managing multiple collections. It provides
    a unified interface for creating, accessing, and managing collections within
    a project namespace.
    """

    def __init__(self, project):
        """Initialize the Project wrapper.

        Args:
            project (IProject): An instance implementing the IProject interface.

        Raises:
            AssertionError: If project is not an instance of IProject.
        """
        assert isinstance(project, IProject), "project must be IProject"
        self.__project = project

    def close(self):
        """Close the project and release all associated resources."""
        self.__project.close()

    def has_collection(self, collection_name):
        """Check if a collection exists in the project.

        Args:
            collection_name (str): Name of the collection to check.

        Returns:
            bool: True if the collection exists, False otherwise.
        """
        return self.__project.has_collection(collection_name)

    def get_collection(self, collection_name):
        """Retrieve a collection by name.

        Args:
            collection_name (str): Name of the collection to retrieve.

        Returns:
            Collection: The requested collection instance, or None if not found.
        """
        return self.__project.get_collection(collection_name)

    def get_collections(self):
        """Get all collections in the project.

        Returns:
            Dict[str, Collection]: Dictionary mapping collection names to Collection instances.
        """
        return self.__project.get_collections()

    def create_collection(self, collection_name, collection_meta):
        """Create a new collection in the project.

        Args:
            collection_name (str): Name for the new collection. Must be unique within the project.
            collection_meta (Dict[str, Any]): Metadata configuration for the collection, including
                fields definition, primary key, vector configuration, etc.

        Returns:
            Collection: The newly created collection instance.

        Raises:
            ValueError: If a collection with the same name already exists or if the metadata is invalid.
        """
        return self.__project.create_collection(collection_name, collection_meta)

    def drop_collection(self, collection_name):
        """Delete a collection from the project.

        Args:
            collection_name (str): Name of the collection to delete.

        Note:
            This operation is irreversible and will permanently delete all data and indexes
            associated with the collection.
        """
        return self.__project.drop_collection(collection_name)
