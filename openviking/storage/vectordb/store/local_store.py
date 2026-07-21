# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from typing import Iterator, List, Tuple, Union

import openviking.storage.vectordb.engine as engine
from openviking.storage.vectordb.store.store import BatchOp, IMutiTableStore, Op, OpType
from openviking.storage.vectordb.utils.stale_lock import clean_stale_rocksdb_locks

# Constant for the maximum Unicode character, used for range queries to cover all possible keys
MAX_UNICODE_CHAR = "\U0010ffff"
STORE_SCAN_PAGE_SIZE = 1024
STORE_SCAN_PAGE_BYTES = 32 * 1024 * 1024


def create_store_engine_proxy(path: str = "") -> "StoreEngineProxy":
    """Create a storage engine proxy.

    Args:
        path (str): Storage path. If empty, creates a volatile (in-memory) storage.
            Otherwise creates persistent storage at the specified path.

    Returns:
        StoreEngineProxy: Proxy instance wrapping the underlying storage engine.
    """
    if path:
        clean_stale_rocksdb_locks(path)
        date_engine = engine.PersistStore(path)
    else:
        date_engine = engine.VolatileStore()
    return StoreEngineProxy(date_engine)


class StoreEngineProxy(IMutiTableStore):
    """Proxy class for storage engine operations.

    Wraps the underlying storage engine to provide a consistent interface
    with table prefixing for multi-table support.

    Attributes:
        storage_engine: Underlying storage engine instance (PersistStore or VolatileStore).
    """

    def __init__(self, storage_engine: Union[engine.PersistStore, engine.VolatileStore]):
        """Initialize the storage engine proxy.

        Args:
            storage_engine: The underlying storage engine instance to wrap.
        """
        super().__init__()
        self.storage_engine = storage_engine

    def read(self, keys: List[str], table_name: str) -> List[bytes]:
        """Read values for multiple keys from a table.

        Args:
            keys (List[str]): List of keys to read.
            table_name (str): Table name prefix.

        Returns:
            List[bytes]: List of values corresponding to the keys.
        """
        if not keys:
            return []
        keys = [table_name + key for key in keys]
        data = self.storage_engine.get_data(keys)
        return data

    def write(self, keys: List[str], values: List[bytes], table_name: str):
        """Write multiple key-value pairs to a table.

        Args:
            keys (List[str]): List of keys to write.
            values (List[bytes]): List of values corresponding to the keys.
            table_name (str): Table name prefix.
        """
        keys = [table_name + key for key in keys]
        self.storage_engine.put_data(keys, values)

    def delete(self, keys: List[str], table_name: str):
        """Delete multiple keys from a table.

        Args:
            keys (List[str]): List of keys to delete.
            table_name (str): Table name prefix.
        """
        keys = [table_name + key for key in keys]
        self.storage_engine.delete_data(keys)

    def clear(self):
        """Clear all data from the storage engine."""
        self.storage_engine.clear_data()

    def read_all(self, table_name: str) -> List[Tuple[str, bytes]]:
        """Read all key-value pairs from a table.

        Args:
            table_name (str): Table name prefix.

        Returns:
            List[Tuple[str, bytes]]: List of (key, value) tuples with table prefix removed from keys.
        """
        start_key = table_name
        # Use max unicode character to cover all possible strings with this prefix
        end_key = table_name + MAX_UNICODE_CHAR
        kv_list = self.storage_engine.seek_range(start_key, end_key)
        return [
            (data[0][len(table_name) :], data[1])
            for data in kv_list
            if data[0].startswith(table_name)
        ]

    def iter_all(
        self,
        table_name: str,
        page_size: int = STORE_SCAN_PAGE_SIZE,
        page_bytes: int = STORE_SCAN_PAGE_BYTES,
    ) -> Iterator[Tuple[str, bytes]]:
        """Scan a table in bounded native pages.

        Pages are capped by row count and ``max(byte budget, one encoded row)``
        so an oversized row cannot cause a false end-of-scan.  Each native
        page is internally consistent, but separate page calls do not share a
        snapshot; collection recovery invokes this while the store is quiescent.
        """
        if page_size <= 0:
            raise ValueError("Store scan page size must be positive")
        if page_bytes <= 0:
            raise ValueError("Store scan page byte budget must be positive")

        seek_page = getattr(self.storage_engine, "seek_range_page", None)
        if not callable(seek_page):
            raise RuntimeError(
                "The native VectorDB engine does not support bounded store scans; "
                "rebuild or reinstall the matching OpenViking engine package"
            )

        end_key = table_name + MAX_UNICODE_CHAR
        cursor = table_name
        start_exclusive = False
        try:
            page = seek_page(cursor, end_key, page_size, page_bytes, start_exclusive)
        except NotImplementedError as exc:
            raise RuntimeError(
                "The native VectorDB engine does not support bounded store scans; "
                "rebuild or reinstall the matching OpenViking engine package"
            ) from exc

        while page:
            next_cursor = page[-1][0]
            if next_cursor < cursor or (start_exclusive and next_cursor <= cursor):
                raise RuntimeError(
                    "Native bounded store scan did not advance its continuation cursor"
                )
            for full_key, value in page:
                if full_key.startswith(table_name):
                    yield full_key[len(table_name) :], value
            # Drop the page before the next native call so two encoded-vector
            # pages are not live at the same time during recovery.
            page = None
            full_key = None
            value = None
            cursor = next_cursor
            start_exclusive = True
            page = seek_page(cursor, end_key, page_size, page_bytes, start_exclusive)

    def begin_to_seek(self, end_key: str, table_name: str) -> List[Tuple[str, bytes]]:
        """Retrieve all entries from the beginning to a specific key.

        Args:
            end_key (str): Ending key (exclusive).
            table_name (str): Table name prefix.

        Returns:
            List[Tuple[str, bytes]]: List of (key, value) tuples with table prefix removed from keys.
        """
        start_key = table_name
        end_key_full = table_name + end_key
        kv_list = self.storage_engine.seek_range(start_key, end_key_full)
        return [
            (data[0][len(table_name) :], data[1])
            for data in kv_list
            if data[0].startswith(table_name)
        ]

    def seek_to_end(self, start_key: str, table_name: str) -> List[Tuple[str, bytes]]:
        """Retrieve all entries from a starting key to the end.

        Args:
            start_key (str): Starting key (inclusive).
            table_name (str): Table name prefix.

        Returns:
            List[Tuple[str, bytes]]: List of (key, value) tuples with table prefix removed from keys.
        """
        start_key_full = table_name + start_key
        end_key = table_name + MAX_UNICODE_CHAR
        kv_list = self.storage_engine.seek_range(start_key_full, end_key)
        return [
            (data[0][len(table_name) :], data[1])
            for data in kv_list
            if data[0].startswith(table_name)
        ]

    def exec_sequence(self, op: List[Op], table_name: str):
        """Execute a sequence of operations on a specific table.

        Args:
            op (List[Op]): List of operations to execute in order.
            table_name (str): Name of the table to operate on.
        """
        engine_op_list = []
        for operation in op:
            engine_op = engine.StorageOp()
            if operation.op_type == OpType.PUT:
                engine_op.type = engine.StorageOpType.PUT
                engine_op.value = operation.data
            else:
                engine_op.type = engine.StorageOpType.DELETE
                engine_op.value = ""  # Value not needed for delete

            engine_op.key = table_name + operation.key
            engine_op_list.append(engine_op)
        self.storage_engine.exec_op(engine_op_list)

    def exec_sequence_batch_op(self, batch_op_list: List[BatchOp]):
        """Execute a batch of operations across multiple tables.

        Args:
            batch_op_list (List[BatchOp]): List of batch operations to execute.
                Each operation can contain multiple PUT or DELETE operations for a specific table.
        """
        engine_op_list = []
        for batch_op in batch_op_list:
            for i, key in enumerate(batch_op.keys):
                engine_op = engine.StorageOp()
                # batch_op.op_type can be a list or a single value
                if isinstance(batch_op.op_type, list):
                    op_type = (
                        batch_op.op_type[i] if i < len(batch_op.op_type) else batch_op.op_type[0]
                    )
                else:
                    op_type = batch_op.op_type

                if op_type == OpType.PUT:
                    engine_op.type = engine.StorageOpType.PUT
                else:
                    engine_op.type = engine.StorageOpType.DELETE

                engine_op.key = batch_op.table + key
                # Safety check for data_list
                engine_op.value = batch_op.data_list[i] if i < len(batch_op.data_list) else ""
                engine_op_list.append(engine_op)
        self.storage_engine.exec_op(engine_op_list)
