# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
import copy
import json
from typing import Any, Dict, Optional

from openviking.storage.vectordb.meta.dict import IDict
from openviking.storage.vectordb.store.file_store import FileStore


class LocalDict(IDict):
    """Local dictionary implementation using a Python dict."""

    def __init__(self, data: Optional[Dict[str, Any]] = None):
        """Initialize LocalDict.

        Args:
            data (Optional[Dict[str, Any]]): Initial data for the dictionary.
        """
        super().__init__()
        self.data = copy.deepcopy(data) if data is not None else {}

    def update(self, data: Dict[str, Any]):
        """Update the dictionary with new data.

        Args:
            data (Dict[str, Any]): The data to merge.
        """
        for key, value in data.items():
            self.data[key] = value

    def override(self, data: Dict[str, Any]):
        """Override the dictionary content with new data.

        Args:
            data (Dict[str, Any]): The new data to replace existing content.
        """
        self.data = data

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from the dictionary.

        Args:
            key (str): The key to retrieve.
            default (Any): The default value if key is not found.

        Returns:
            Any: The value associated with the key, or default.
        """
        return self.data.get(key, default)

    def drop(self):
        """Clear the dictionary content."""
        self.data = {}

    def get_raw(self) -> Dict[str, Any]:
        """Get the raw dictionary data (reference).

        Returns:
            Dict[str, Any]: The raw dictionary data.
        """
        return self.data

    def get_raw_copy(self) -> Dict[str, Any]:
        """Get a deep copy of the raw dictionary data.

        Returns:
            Dict[str, Any]: A deep copy of the dictionary data.
        """
        return copy.deepcopy(self.data)


class VolatileDict(LocalDict):
    """A volatile (in-memory) dictionary implementation."""

    def __init__(self, data: Optional[Dict[str, Any]] = None):
        """Initialize VolatileDict.

        Args:
            data (Optional[Dict[str, Any]]): Initial data for the dictionary.
        """
        super().__init__(data)


class PersistentDict(LocalDict):
    """A persistent dictionary implementation backed by file storage."""

    def __init__(self, path: str, data: Optional[Dict[str, Any]] = None):
        """Initialize PersistentDict.

        Args:
            path (str): The file path for persistence.
            data (Optional[Dict[str, Any]]): Initial data to merge if file doesn't exist or is empty.
        """
        super().__init__(data)
        self.path = path
        self.storage = FileStore()
        bytes_data = self.storage.get(self.path)
        try:
            init_data = json.loads(bytes_data.decode()) if bytes_data else {}
        except json.JSONDecodeError:
            # Handle corrupted or invalid JSON gracefully
            init_data = {}
        self.update(init_data)

    def override(self, data: Dict[str, Any]):
        """Override the dictionary content and persist to file.

        Args:
            data (Dict[str, Any]): The new data to replace existing content.
        """
        super().override(data)
        self._persist()

    def update(self, data: Dict[str, Any]):
        """Update the dictionary and persist to file.

        Args:
            data (Dict[str, Any]): The data to merge.
        """
        super().update(data)
        self._persist()

    def _persist(self):
        """Persist the current state to file.

        Note:
            This performs a full serialization and write of the dictionary.
            Suitable for metadata which is typically small and infrequently updated.
            FileStore.put ensures atomic writes.
        """
        bytes_data = json.dumps(self.data).encode()
        self.storage.put(self.path, bytes_data)

    def drop(self):
        """Clear the dictionary content and delete the persistence file."""
        super().drop()
        self.storage.delete(self.path)
