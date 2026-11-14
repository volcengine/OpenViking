# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import types

import pytest

from openviking.storage.vectordb.store import store_manager as store_manager_module
from openviking.storage.vectordb.store.data import CandidateData, DeltaRecord
from openviking.storage.vectordb.store.store import IMutiTableStore, OpType
from openviking.storage.vectordb.store.store_manager import StoreManager


class _MemoryMultiTableStore(IMutiTableStore):
    """In-memory store with the ordered, last-write-wins semantics of the real one."""

    def __init__(self):
        self.tables: dict[str, dict[str, bytes]] = {}

    def read(self, keys, table_name):
        table = self.tables.get(table_name, {})
        return [table.get(key, b"") for key in keys]

    def write(self, keys, values, table_name):
        table = self.tables.setdefault(table_name, {})
        for key, value in zip(keys, values, strict=True):
            table[key] = value

    def delete(self, keys, table_name):
        table = self.tables.setdefault(table_name, {})
        for key in keys:
            table.pop(key, None)

    def clear(self):
        self.tables.clear()

    def read_all(self, table_name):
        return sorted(self.tables.get(table_name, {}).items())

    def seek_to_end(self, key, table_name):
        return [(k, v) for k, v in self.read_all(table_name) if k >= key]

    def begin_to_seek(self, key, table_name):
        return [(k, v) for k, v in self.read_all(table_name) if k < key]

    def exec_sequence(self, op, table_name):
        table = self.tables.setdefault(table_name, {})
        for item in op:
            if item.op_type == OpType.PUT:
                table[item.key] = item.data
            else:
                table.pop(item.key, None)

    def exec_sequence_batch_op(self, op):
        for batch in op:
            types_ = batch.op_type
            if not isinstance(types_, list):
                types_ = [types_] * len(batch.keys)
            table = self.tables.setdefault(batch.table, {})
            for key, op_type, data in zip(batch.keys, types_, batch.data_list, strict=True):
                if op_type == OpType.PUT:
                    table[key] = data
                else:
                    table.pop(key, None)


@pytest.fixture(autouse=True)
def _serialize_dataclasses_one_by_one(monkeypatch):
    """Keep the test independent of the packaged native engine.

    Without the native engine ``BytesRow`` falls back to the pure-Python row,
    whose ``serialize_batch`` only accepts mappings while ``serialize_list``
    passes dataclass instances. Serializing object by object is what the native
    batch serializer does semantically, so the TTL bookkeeping can be tested in
    both environments.
    """

    def serialize_list(_cls, objects):
        return [obj.serialize() for obj in objects]

    monkeypatch.setattr(CandidateData, "serialize_list", classmethod(serialize_list))
    monkeypatch.setattr(DeltaRecord, "serialize_list", classmethod(serialize_list))


@pytest.fixture
def clock(monkeypatch):
    now = [1_000_000_000_000]
    monkeypatch.setattr(
        store_manager_module,
        "time",
        types.SimpleNamespace(time_ns=lambda: now[0]),
    )
    return now


def test_expire_data_expires_every_candidate_of_one_ttl_batch(clock):
    store = _MemoryMultiTableStore()
    manager = StoreManager(store)

    manager.add_cands_data(
        [
            CandidateData(label=label, vector=[1.0, 0.0], fields=f"field-{label}")
            for label in (1, 2, 3)
        ],
        ttl=60,
    )
    clock[0] += 61_000_000_000

    expired = manager.expire_data()

    assert sorted(record.label for record in expired) == [1, 2, 3]
    assert manager.fetch_cands_data([1, 2, 3]) == [None, None, None]
    assert store.read_all(StoreManager.TTLTable) == []


def test_expire_data_keeps_a_record_rewritten_without_ttl(clock):
    store = _MemoryMultiTableStore()
    manager = StoreManager(store)

    manager.add_cands_data(
        [CandidateData(label=7, vector=[1.0, 0.0], fields="old")],
        ttl=60,
    )
    clock[0] += 61_000_000_000
    manager.add_cands_data(
        [CandidateData(label=7, vector=[1.0, 0.0], fields="permanent")],
        ttl=0,
    )

    expired = manager.expire_data()

    assert expired == []
    [candidate] = manager.fetch_cands_data([7])
    assert candidate is not None
    assert candidate.fields == "permanent"
    assert store.read_all(StoreManager.TTLTable) == []
