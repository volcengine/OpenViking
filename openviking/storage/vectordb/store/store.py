# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, List, Tuple, Union


class IKVStore(ABC):
    """Interface for key-value storage implementations.

    Provides a simple abstraction for key-value operations that can be implemented
    by various storage backends (e.g., in-memory, persistent, distributed).
    """

    def __init__(self):
        pass

    @abstractmethod
    def get(self, key):
        """Retrieve a value by its key.

        Args:
            key: The key to retrieve the value for.

        Returns:
            The value associated with the key, or None if not found.

        Raises:
            NotImplementedError: If not implemented by subclass.
        """
        raise NotImplementedError

    @abstractmethod
    def put(self, key, value):
        """Store or update a key-value pair.

        Args:
            key: The key to store the value under.
            value: The value to store.

        Raises:
            NotImplementedError: If not implemented by subclass.
        """
        raise NotImplementedError

    @abstractmethod
    def delete(self, key):
        """Delete a key-value pair.

        Args:
            key: The key to delete.

        Raises:
            NotImplementedError: If not implemented by subclass.
        """
        raise NotImplementedError


class OpType(Enum):
    """Enumeration of storage operation types."""

    PUT = 0  # Insert or update operation
    DEL = 1  # Delete operation


class Op:
    """Represents a single storage operation.

    Used for batch processing of storage operations.
    """

    def __init__(self, op_type: OpType, key: str, data: Any):
        """Initialize a storage operation.

        Args:
            op_type (OpType): Type of operation (PUT or DEL).
            key: The key for this operation.
            data: The data for this operation (relevant for PUT operations).
        """
        self.op_type = op_type
        self.key = key
        self.data = data


class BatchOp:
    """Represents a batch of storage operations on a specific table.

    Allows for efficient execution of multiple operations in a single call.
    """

    def __init__(
        self,
        table: str,
        op_type: Union[OpType, List[OpType]],
        keys: List[str],
        data_list: List[Any],  # Can be bytes or str
    ):
        """Initialize a batch operation.

        Args:
            table (str): Name of the table to operate on.
            op_type (Union[OpType, List[OpType]]): Operation type or list of types for each key.
            keys (List[str]): List of keys to operate on.
            data_list (List[Any]): List of data values corresponding to each key.
        """
        self.table = table
        self.op_type = op_type
        self.keys = keys
        self.data_list = data_list


class IMutiTableStore(ABC):
    """Interface for multi-table storage implementations.

    Provides operations for managing data across multiple tables with support
    for batch operations and range queries.
    """

    def __init__(self):
        pass

    @abstractmethod
    def read(self, keys: List[str], table_name: str) -> List[bytes]:
        """Read values for multiple keys from a specific table.

        Args:
            keys (List[str]): List of keys to read.
            table_name (str): Name of the table to read from.

        Returns:
            List[bytes]: List of values corresponding to the keys.

        Raises:
            NotImplementedError: If not implemented by subclass.
        """
        pass

    @abstractmethod
    def write(self, keys: List[str], values: List[bytes], table_name: str):
        """Write multiple key-value pairs to a specific table.

        Args:
            keys (List[str]): List of keys to write.
            values (List[bytes]): List of values corresponding to the keys.
            table_name (str): Name of the table to write to.

        Raises:
            NotImplementedError: If not implemented by subclass.
        """
        pass

    @abstractmethod
    def delete(self, keys: List[str], table_name: str):
        """Delete multiple keys from a specific table.

        Args:
            keys (List[str]): List of keys to delete.
            table_name (str): Name of the table to delete from.

        Raises:
            NotImplementedError: If not implemented by subclass.
        """
        pass

    @abstractmethod
    def clear(self):
        """Clear all data from all tables.

        Warning:
            This operation is irreversible and will delete all data.

        Raises:
            NotImplementedError: If not implemented by subclass.
        """
        pass

    @abstractmethod
    def read_all(self, table_name: str) -> List[Tuple[str, bytes]]:
        """Read all key-value pairs from a table.

        Args:
            table_name (str): Table name prefix.

        Returns:
            List[Tuple[str, bytes]]: List of (key, value) tuples with table prefix removed from keys.

        Raises:
            NotImplementedError: If not implemented by subclass.
        """
        pass

    @abstractmethod
    def seek_to_end(self, key: str, table_name: str) -> List[Tuple[str, bytes]]:
        """Retrieve all entries from a starting key to the end of the table.

        Args:
            key (str): Starting key for the range query (inclusive).
            table_name (str): Name of the table to query.

        Returns:
            List[Tuple[str, bytes]]: List of (key, value) tuples from the starting key to the end.

        Raises:
            NotImplementedError: If not implemented by subclass.
        """
        pass

    @abstractmethod
    def begin_to_seek(self, key: str, table_name: str) -> List[Tuple[str, bytes]]:
        """Retrieve all entries from the beginning of the table to a specific key.

        Args:
            key (str): Ending key for the range query (inclusive).
            table_name (str): Name of the table to query.

        Returns:
            List[Tuple[str, bytes]]: List of (key, value) tuples from the beginning to the ending key.

        Raises:
            NotImplementedError: If not implemented by subclass.
        """
        pass

    @abstractmethod
    def exec_sequence(self, op: List[Op], table_name: str):
        """Execute a sequence of operations on a specific table.

        Args:
            op (List[Op]): List of operations to execute in order.
            table_name (str): Name of the table to operate on.

        Raises:
            NotImplementedError: If not implemented by subclass.
        """
        pass

    @abstractmethod
    def exec_sequence_batch_op(self, op: List[BatchOp]):
        """Execute a batch of operations across multiple tables.

        Args:
            op (List[BatchOp]): List of batch operations to execute.

        Raises:
            NotImplementedError: If not implemented by subclass.
        """
        pass
