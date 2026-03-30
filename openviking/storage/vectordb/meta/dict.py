# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from abc import ABC, abstractmethod
from typing import Any
from typing import Dict as TypeDict


class Dict:
    """A wrapper class for IDict.

    Delegates operations to the underlying IDict implementation.
    """

    def __init__(self, idict: "IDict"):
        """Initialize Dict wrapper.

        Args:
            idict (IDict): The IDict implementation to wrap.
        """
        assert isinstance(idict, IDict), "idict must be a IDict"
        self.__idict = idict

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from the dictionary.

        Args:
            key (str): The key to retrieve.
            default (Any): The default value if key is not found.

        Returns:
            Any: The value associated with the key, or default.
        """
        return self.__idict.get(key, default)

    def drop(self):
        """Clear the dictionary content."""
        self.__idict.drop()

    def update(self, data: TypeDict[str, Any]):
        """Update the dictionary with new data.

        Args:
            data (Dict[str, Any]): The data to merge into the dictionary.
        """
        self.__idict.update(data)

    def get_raw_copy(self) -> TypeDict[str, Any]:
        """Get a deep copy of the raw dictionary data.

        Returns:
            Dict[str, Any]: A deep copy of the dictionary data.
        """
        return self.__idict.get_raw_copy()

    def get_raw(self) -> TypeDict[str, Any]:
        """Get the raw dictionary data (reference).

        Returns:
            Dict[str, Any]: The raw dictionary data.
        """
        return self.__idict.get_raw()


class IDict(ABC):
    """Interface for dictionary-like storage implementations."""

    def __init__(self):
        pass

    @abstractmethod
    def update(self, datas: TypeDict[str, Any]):
        """Update the dictionary with new data.

        Args:
            datas (Dict[str, Any]): The data to merge.
        """
        raise NotImplementedError

    @abstractmethod
    def override(self, data: TypeDict[str, Any]):
        """Override the dictionary content with new data.

        Args:
            data (Dict[str, Any]): The new data to replace existing content.
        """
        raise NotImplementedError

    @abstractmethod
    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from the dictionary.

        Args:
            key (str): The key to retrieve.
            default (Any): The default value if key is not found.

        Returns:
            Any: The value associated with the key, or default.
        """
        raise NotImplementedError

    @abstractmethod
    def drop(self):
        """Clear the dictionary content."""
        raise NotImplementedError

    @abstractmethod
    def get_raw_copy(self) -> TypeDict[str, Any]:
        """Get a deep copy of the raw dictionary data.

        Returns:
            Dict[str, Any]: A deep copy of the dictionary data.
        """
        raise NotImplementedError

    @abstractmethod
    def get_raw(self) -> TypeDict[str, Any]:
        """Get the raw dictionary data (reference).

        Returns:
            Dict[str, Any]: The raw dictionary data.
        """
        raise NotImplementedError
