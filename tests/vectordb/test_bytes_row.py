# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import json
import random
import string
import unittest
from dataclasses import dataclass, field
from typing import List

from openviking.storage.vectordb import engine
from openviking.storage.vectordb.store.bytes_row import (
    FieldType,
    _PyBytesRow,
    _PyFieldType,
    _PySchema,
)
from openviking.storage.vectordb.store.serializable import serializable


# Define a complex data structure for testing consistency
@serializable
@dataclass
class ComplexData:
    label: int = 0
    vector: List[float] = field(default_factory=list)
    sparse_raw_terms: List[str] = field(default_factory=list)
    sparse_values: List[float] = field(default_factory=list)
    fields: str = ""
    expire_ns_ts: int = 0
    is_deleted: bool = False


class TestBytesRow(unittest.TestCase):
    def test_basic_serialization(self):
        @serializable
        @dataclass
        class BasicData:
            id: int = field(default=0, metadata={"field_type": FieldType.int64})
            score: float = 0.0
            active: bool = False
            name: str = ""

        data = BasicData(id=1234567890, score=0.95, active=True, name="viking_db")

        # Serialize
        serialized = data.serialize()
        self.assertIsInstance(serialized, bytes)

        # Deserialize whole row
        deserialized = BasicData.from_bytes(serialized)
        self.assertEqual(deserialized.id, 1234567890)
        self.assertAlmostEqual(deserialized.score, 0.95, places=5)
        self.assertEqual(deserialized.active, True)
        self.assertEqual(deserialized.name, "viking_db")

        # Deserialize single field
        val_id = BasicData.bytes_row.deserialize_field(serialized, "id")
        self.assertEqual(val_id, 1234567890)

        val_name = BasicData.bytes_row.deserialize_field(serialized, "name")
        self.assertEqual(val_name, "viking_db")

    def test_list_types(self):
        @serializable
        @dataclass
        class ListData:
            tags: List[str] = field(default_factory=list)
            embedding: List[float] = field(default_factory=list)
            counts: List[int] = field(default_factory=list)

        data = ListData(
            tags=["AI", "Vector", "Search"], embedding=[0.1, 0.2, 0.3, 0.4], counts=[1, 10, 100]
        )

        serialized = data.serialize()
        deserialized = ListData.from_bytes(serialized)

        self.assertEqual(deserialized.tags, ["AI", "Vector", "Search"])
        self.assertEqual(len(deserialized.embedding), 4)
        for i, v in enumerate([0.1, 0.2, 0.3, 0.4]):
            self.assertAlmostEqual(deserialized.embedding[i], v, places=5)
        self.assertEqual(deserialized.counts, [1, 10, 100])

    def test_default_values(self):
        @serializable
        @dataclass
        class DefaultData:
            id: int = field(default=999, metadata={"field_type": FieldType.int64})
            desc: str = "default"

        # Empty data, should use defaults
        data = DefaultData()
        serialized = data.serialize()
        deserialized = DefaultData.from_bytes(serialized)

        self.assertEqual(deserialized.id, 999)
        self.assertEqual(deserialized.desc, "default")

    def test_unicode_strings(self):
        @serializable
        @dataclass
        class UnicodeData:
            text: str = ""

        text = "你好，世界！🌍"
        data = UnicodeData(text=text)
        serialized = data.serialize()
        val = UnicodeData.bytes_row.deserialize_field(serialized, "text")
        self.assertEqual(val, text)

    def test_binary_data(self):
        @serializable
        @dataclass
        class BinaryData:
            raw: bytes = b""

        blob = b"\x00\x01\x02\xff\xfe"
        data = BinaryData(raw=blob)
        serialized = data.serialize()
        val = BinaryData.bytes_row.deserialize_field(serialized, "raw")
        self.assertEqual(val, blob)

    def test_schema_id_validation(self):
        with self.assertRaises(ValueError):
            engine.Schema(
                [
                    {"name": "id", "data_type": engine.FieldType.int64, "id": 0},
                    {"name": "name", "data_type": engine.FieldType.string, "id": 2},
                ]
            )

        with self.assertRaises(ValueError):
            engine.Schema(
                [
                    {"name": "id", "data_type": engine.FieldType.int64, "id": 0},
                    {"name": "dup", "data_type": engine.FieldType.string, "id": 0},
                ]
            )

    def test_missing_fields_use_defaults(self):
        schema = engine.Schema(
            [
                {
                    "name": "id",
                    "data_type": engine.FieldType.int64,
                    "id": 0,
                    "default_value": 7,
                },
                {
                    "name": "name",
                    "data_type": engine.FieldType.string,
                    "id": 1,
                    "default_value": "fallback",
                },
                {
                    "name": "tags",
                    "data_type": engine.FieldType.list_string,
                    "id": 2,
                    "default_value": ["a", "b"],
                },
                {
                    "name": "score",
                    "data_type": engine.FieldType.float32,
                    "id": 3,
                },
            ]
        )
        row = engine.BytesRow(schema)

        serialized = row.serialize({"id": 5})

        self.assertEqual(row.deserialize_field(serialized, "id"), 5)
        self.assertEqual(row.deserialize_field(serialized, "name"), "fallback")
        self.assertEqual(row.deserialize_field(serialized, "tags"), ["a", "b"])
        self.assertAlmostEqual(row.deserialize_field(serialized, "score"), 0.0, places=5)


class TestBytesRowConsistency(unittest.TestCase):
    def setUp(self):
        # Create C++ Schema equivalent to ComplexData
        # Note: IDs must match the order in ComplexData (0-indexed)
        self.cpp_fields = [
            {"name": "label", "data_type": engine.FieldType.int64, "id": 0},
            {"name": "vector", "data_type": engine.FieldType.list_float32, "id": 1},
            {"name": "sparse_raw_terms", "data_type": engine.FieldType.list_string, "id": 2},
            {"name": "sparse_values", "data_type": engine.FieldType.list_float32, "id": 3},
            {"name": "fields", "data_type": engine.FieldType.string, "id": 4},
            {"name": "expire_ns_ts", "data_type": engine.FieldType.int64, "id": 5},
            {"name": "is_deleted", "data_type": engine.FieldType.boolean, "id": 6},
        ]
        self.cpp_schema = engine.Schema(self.cpp_fields)
        self.cpp_row = engine.BytesRow(self.cpp_schema)

        # Create Python Schema equivalent to ComplexData
        self.py_fields = [
            {"name": "label", "data_type": _PyFieldType.int64, "id": 0},
            {"name": "vector", "data_type": _PyFieldType.list_float32, "id": 1},
            {"name": "sparse_raw_terms", "data_type": _PyFieldType.list_string, "id": 2},
            {"name": "sparse_values", "data_type": _PyFieldType.list_float32, "id": 3},
            {"name": "fields", "data_type": _PyFieldType.string, "id": 4},
            {"name": "expire_ns_ts", "data_type": _PyFieldType.int64, "id": 5},
            {"name": "is_deleted", "data_type": _PyFieldType.boolean, "id": 6},
        ]
        self.py_schema = _PySchema(self.py_fields)
        self.py_row = _PyBytesRow(self.py_schema)

    def generate_random_data(self):
        dim = 128
        sparse_dim = 10

        return {
            "label": random.randint(0, 1000000),
            "vector": [random.random() for _ in range(dim)],
            "sparse_raw_terms": [
                "".join(random.choices(string.ascii_letters, k=5)) for _ in range(sparse_dim)
            ],
            "sparse_values": [random.random() for _ in range(sparse_dim)],
            "fields": json.dumps(
                {"key": "value", "data": "".join(random.choices(string.ascii_letters, k=50))}
            ),
            "expire_ns_ts": 1234567890,
            "is_deleted": random.choice([True, False]),
        }

    def test_py_write_cpp_read(self):
        """Test Python serialization -> C++ deserialization"""
        data_dict = self.generate_random_data()

        # Python Serialize (using pure Python impl)
        py_bytes = self.py_row.serialize(data_dict)

        # C++ Deserialize (using ComplexData via serializable or direct engine usage)
        # Here we use direct engine usage to be explicit
        cpp_res = self.cpp_row.deserialize(py_bytes)

        # Verify
        self.assertEqual(cpp_res["label"], data_dict["label"])
        self.assertEqual(len(cpp_res["vector"]), len(data_dict["vector"]))
        for a, b in zip(cpp_res["vector"], data_dict["vector"]):
            self.assertAlmostEqual(a, b, places=5)

        self.assertEqual(cpp_res["sparse_raw_terms"], data_dict["sparse_raw_terms"])

        for a, b in zip(cpp_res["sparse_values"], data_dict["sparse_values"]):
            self.assertAlmostEqual(a, b, places=5)

        self.assertEqual(cpp_res["fields"], data_dict["fields"])
        self.assertEqual(cpp_res["expire_ns_ts"], data_dict["expire_ns_ts"])
        self.assertEqual(cpp_res["is_deleted"], data_dict["is_deleted"])

    def test_cpp_write_py_read(self):
        """Test C++ serialization -> Python deserialization"""
        data_dict = self.generate_random_data()

        # C++ Serialize
        cpp_bytes = self.cpp_row.serialize(data_dict)

        # Python Deserialize
        py_res = self.py_row.deserialize(cpp_bytes)

        # Verify
        self.assertEqual(py_res["label"], data_dict["label"])
        # Check vector with almost equal
        for a, b in zip(py_res["vector"], data_dict["vector"]):
            self.assertAlmostEqual(a, b, places=5)

        self.assertEqual(py_res["sparse_raw_terms"], data_dict["sparse_raw_terms"])

        for a, b in zip(py_res["sparse_values"], data_dict["sparse_values"]):
            self.assertAlmostEqual(a, b, places=5)

        self.assertEqual(py_res["fields"], data_dict["fields"])
        self.assertEqual(py_res["expire_ns_ts"], data_dict["expire_ns_ts"])
        self.assertEqual(py_res["is_deleted"], data_dict["is_deleted"])

    def test_binary_consistency(self):
        """Test that C++ and Python produce identical binary output"""
        data_dict = self.generate_random_data()

        py_bytes = self.py_row.serialize(data_dict)
        cpp_bytes = self.cpp_row.serialize(data_dict)

        self.assertEqual(len(py_bytes), len(cpp_bytes), "Binary length mismatch")
        self.assertEqual(py_bytes, cpp_bytes, "Binary content mismatch")


if __name__ == "__main__":
    unittest.main()
