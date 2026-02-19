# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import time
from typing import List, Optional, Tuple

from openviking.storage.vectordb.store.data import CandidateData, DeltaRecord
from openviking.storage.vectordb.store.local_store import create_store_engine_proxy
from openviking.storage.vectordb.store.store import BatchOp, IMutiTableStore, OpType
from openviking.storage.vectordb.utils.constants import TableNames


def create_store_manager(type: str, path: str = "") -> "StoreManager":
    """Create a store manager based on type and path.

    Args:
        type (str): Storage type (e.g., "local").
        path (str): Storage path.

    Returns:
        StoreManager: The created store manager.

    Raises:
        ValueError: If the storage type is unknown.
    """
    if type == "local":
        storage_engine = create_store_engine_proxy(path)
        return StoreManager(storage_engine)
    else:
        raise ValueError(f"unknown storage type {type}")


class StoreManager:
    """Manager for higher-level storage operations involving candidates, deltas, and TTL.

    Attributes:
        CandsTable (str): Table name for candidates.
        DeltaTable (str): Table name for delta records.
        TTLTable (str): Table name for Time-To-Live records.
        storage (IMutiTableStore): The underlying multi-table storage.
    """

    CandsTable = TableNames.CANDIDATES.value
    DeltaTable = TableNames.DELTA.value
    TTLTable = TableNames.TTL.value

    def __init__(self, storage_engine: IMutiTableStore):
        """Initialize the store manager.

        Args:
            storage_engine (IMutiTableStore): The underlying storage engine.
        """
        self.storage = storage_engine

    def add_cands_data(
        self, cands_list: List[CandidateData], ttl: int = 0, need_delta: bool = True
    ) -> List[DeltaRecord]:
        """Add candidate data to the store.

        Args:
            cands_list (List[CandidateData]): List of candidate data objects to add.
            ttl (int): Time-To-Live in seconds. 0 means no expiration.
            need_delta (bool): Whether to record delta changes.

        Returns:
            List[DeltaRecord]: List of generated delta records.
        """
        delta_list = []
        batch_op_list = []

        if need_delta:
            bytes_list = self.storage.read(
                [str(data.label) for data in cands_list],
                StoreManager.CandsTable,
            )
            old_cands_fields_list = [
                (
                    CandidateData.bytes_row.deserialize_field(bytes_data, "fields")
                    if bytes_data
                    else ""
                )
                for bytes_data in bytes_list
            ]
            delta_list = [DeltaRecord(type=DeltaRecord.Type.UPSERT) for _ in range(len(cands_list))]
            for i, old_fields in enumerate(old_cands_fields_list):
                delta_list[i].label = cands_list[i].label
                delta_list[i].vector = cands_list[i].vector
                delta_list[i].sparse_raw_terms = cands_list[i].sparse_raw_terms
                delta_list[i].sparse_values = cands_list[i].sparse_values
                delta_list[i].fields = cands_list[i].fields
                delta_list[i].old_fields = old_fields

            base_ts = time.time_ns()
            batch_op_list.append(
                BatchOp(
                    StoreManager.DeltaTable,
                    [OpType.PUT] * len(delta_list),
                    [str(base_ts + i) for i in range(len(delta_list))],
                    DeltaRecord.serialize_list(delta_list),
                )
            )

        if ttl > 0:
            expire_ns = time.time_ns() + ttl * 1_000_000_000
            for data in cands_list:
                data.expire_ns_ts = expire_ns

        batch_op_list.append(
            BatchOp(
                StoreManager.CandsTable,
                [OpType.PUT] * len(cands_list),
                [str(data.label) for data in cands_list],
                CandidateData.serialize_list(cands_list),
            )
        )

        if ttl > 0:
            batch_op_list.append(
                BatchOp(
                    StoreManager.TTLTable,
                    [OpType.PUT] * len(cands_list),
                    [str(data.expire_ns_ts) for data in cands_list],
                    [str(data.label).encode("utf-8") for data in cands_list],
                )
            )

        self.storage.exec_sequence_batch_op(batch_op_list)

        return delta_list

    def delete_data(
        self, label_list: List[int], need_record_delta: bool = True
    ) -> List[DeltaRecord]:
        """Delete data by labels.

        Args:
            label_list (List[int]): List of labels to delete.
            need_record_delta (bool): Whether to record delta changes.

        Returns:
            List[DeltaRecord]: List of generated delta records (for deletions).
        """
        delta_list = []
        batch_op_list = []
        if need_record_delta:
            bytes_list = self.storage.read(
                [str(label) for label in label_list],
                StoreManager.CandsTable,
            )
            old_cands_fields_list = [
                (
                    CandidateData.bytes_row.deserialize_field(bytes_data, "fields")
                    if bytes_data
                    else ""
                )
                for bytes_data in bytes_list
            ]
            delta_list = [DeltaRecord(type=DeltaRecord.Type.DELETE) for _ in range(len(label_list))]
            for i, data in enumerate(old_cands_fields_list):
                delta_list[i].label = label_list[i]
                delta_list[i].old_fields = data
            base_ts = time.time_ns()
            batch_op_list.append(
                BatchOp(
                    StoreManager.DeltaTable,
                    [OpType.PUT] * len(delta_list),
                    [str(base_ts + i) for i in range(len(delta_list))],
                    DeltaRecord.serialize_list(delta_list),
                )
            )

        batch_op_list.append(
            BatchOp(
                StoreManager.CandsTable,
                [OpType.DEL] * len(label_list),
                [str(label) for label in label_list],
                [b"" for _ in range(len(label_list))],
            )
        )
        self.storage.exec_sequence_batch_op(batch_op_list)
        return delta_list

    def fetch_cands_data(self, label_list: List[int]) -> List[Optional[CandidateData]]:
        """Fetch candidate data by labels.

        Args:
            label_list (List[int]): List of labels to fetch.

        Returns:
            List[Optional[CandidateData]]: List of candidate data objects, or None if not found.
        """
        bytes_list = self.storage.read(
            [str(label) for label in label_list],
            StoreManager.CandsTable,
        )
        cands_list = [
            CandidateData.from_bytes(bytes_data) if bytes_data else None
            for bytes_data in bytes_list
        ]
        return cands_list

    def get_all_cands_data(self) -> List[CandidateData]:
        """Get all candidate data from the store.

        Returns:
            List[CandidateData]: List of all candidate data objects.
        """
        cands_kv_list = self.storage.read_all(StoreManager.CandsTable)
        cands_list = [CandidateData.from_bytes(data[1]) for data in cands_kv_list]
        return cands_list

    def clear(self):
        """Clear all data from the store."""
        self.storage.clear()

    def get_delta_data_after_ts(self, ns_ts: int) -> List[DeltaRecord]:
        """Get delta records created after a specific timestamp.

        Args:
            ns_ts (int): Timestamp in nanoseconds.

        Returns:
            List[DeltaRecord]: List of delta records.
        """
        delta_kv_list = self.storage.seek_to_end(
            str(ns_ts),
            StoreManager.DeltaTable,
        )
        delta_list = [DeltaRecord.from_bytes(data=data[1]) for data in delta_kv_list]
        return delta_list

    def delete_delta_data_before_ts(self, ns_ts: int) -> List[DeltaRecord]:
        """Delete delta records created before a specific timestamp.

        Args:
            ns_ts (int): Timestamp in nanoseconds.

        Returns:
            List[DeltaRecord]: List of deleted delta records.
        """
        delta_kv_list = self.storage.begin_to_seek(
            str(ns_ts),
            StoreManager.DeltaTable,
        )
        delta_list = [DeltaRecord.from_bytes(data=data[1]) for data in delta_kv_list]
        delta_keys = [data[0] for data in delta_kv_list]
        self.storage.delete(delta_keys, StoreManager.DeltaTable)
        return delta_list

    def expire_data(self) -> List[DeltaRecord]:
        """Process expired data based on TTL.

        Returns:
            List[DeltaRecord]: List of delta records for expired data.
        """
        now_time = time.time_ns()
        ttl_kv_list = self.storage.begin_to_seek(
            str(now_time),
            StoreManager.TTLTable,
        )

        label_list = [str(data[1].decode("utf-8")) for data in ttl_kv_list]

        cands_bytes_list = self.storage.read(
            label_list,
            StoreManager.CandsTable,
        )

        # Optimize: Avoid full deserialization if only checking expire_ns_ts
        # But we need label and fields later for DeltaRecord.
        # Let's filter first by expire_ns_ts which is in CandidateData.

        expired_cands_data: List[Tuple[int, str]] = []

        for byte_data in cands_bytes_list:
            if not byte_data:
                continue

            # Efficiently check expiration without full object creation if possible
            # But CandidateData takes bytes_data in init, so we might as well use helper
            expire_ts = CandidateData.bytes_row.deserialize_field(byte_data, "expire_ns_ts")
            if expire_ts <= now_time:
                label = CandidateData.bytes_row.deserialize_field(byte_data, "label")
                fields = CandidateData.bytes_row.deserialize_field(byte_data, "fields")
                expired_cands_data.append((label, fields))

        batch_op_list = []
        delta_list = [
            DeltaRecord(type=DeltaRecord.Type.DELETE) for _ in range(len(expired_cands_data))
        ]

        if expired_cands_data:
            batch_op_list.append(
                BatchOp(
                    StoreManager.CandsTable,
                    [OpType.DEL] * len(expired_cands_data),
                    [str(data[0]) for data in expired_cands_data],
                    ["" for _ in range(len(expired_cands_data))],
                )
            )
            for i, data in enumerate(expired_cands_data):
                delta_list[i].label = data[0]
                delta_list[i].old_fields = data[1]

            base_ts = time.time_ns()
            batch_op_list.append(
                BatchOp(
                    StoreManager.DeltaTable,
                    [OpType.PUT] * len(delta_list),
                    [str(base_ts + i) for i in range(len(delta_list))],
                    DeltaRecord.serialize_list(delta_list),
                )
            )

        if ttl_kv_list:
            batch_op_list.append(
                BatchOp(
                    StoreManager.TTLTable,
                    [OpType.DEL] * len(ttl_kv_list),
                    [data[0] for data in ttl_kv_list],
                    ["" for _ in range(len(ttl_kv_list))],
                )
            )

        self.storage.exec_sequence_batch_op(batch_op_list)
        return delta_list
