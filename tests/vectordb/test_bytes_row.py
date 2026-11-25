# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
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


class TestBytesRow(unittest.TestCase):
    def test_dataclass_defaults_and_typed_fields_roundtrip(self):
        @serializable
        @dataclass
        class Record:
            id: int = field(default=999, metadata={"field_type": FieldType.int64})
            score: float = 0.0
            active: bool = False
            name: str = "default"
            tags: List[str] = field(default_factory=list)
            embedding: List[float] = field(default_factory=list)
            counts: List[int] = field(default_factory=list)
            raw: bytes = b""

        data = Record(
            id=2**40,
            score=0.9375,
            active=True,
            name="你好，世界！🌍",
            tags=["AI", "向量"],
            embedding=[0.125, -0.25, 0.5],
            counts=[1, -10, 100],
            raw=b"\x00\x01\xff\xfe",
        )

        serialized = data.serialize()
        self.assertIsInstance(serialized, bytes)
        self.assertEqual(Record.from_bytes(serialized), data)
        for name in ("id", "name", "raw"):
            self.assertEqual(
                Record.bytes_row.deserialize_field(serialized, name), getattr(data, name)
            )

        defaults = Record.from_bytes(Record().serialize())
        self.assertEqual((defaults.id, defaults.name), (999, "default"))

    def test_oversized_string_serialization_raises(self):
        @serializable
        @dataclass
        class LargeStringData:
            text: str = ""

        text = "x" * 65536
        with self.assertRaisesRegex((RuntimeError, ValueError), "text.*exceeds 65535 bytes"):
            LargeStringData(text=text).serialize()

        py_schema = _PySchema([{"name": "text", "data_type": _PyFieldType.string, "id": 0}])
        py_row = _PyBytesRow(py_schema)
        with self.assertRaisesRegex(ValueError, "text.*exceeds 65535 bytes"):
            py_row.serialize({"text": text})

    def test_oversized_list_string_element_serialization_raises(self):
        # A list<string> element uses the same UINT16 length prefix as a scalar
        # string, so it must reject >65535-byte elements with the same clean,
        # field-attributed ValueError — not a raw struct.error from deep inside
        # struct.pack_into.
        py_schema = _PySchema([{"name": "tags", "data_type": _PyFieldType.list_string, "id": 0}])
        py_row = _PyBytesRow(py_schema)
        with self.assertRaisesRegex(ValueError, "tags.*exceeds 65535 bytes"):
            py_row.serialize({"tags": ["x" * 65536, "ok"]})

        # A list of in-bounds strings (including multibyte) still round-trips.
        blob = py_row.serialize({"tags": ["hello", "世界"]})
        self.assertEqual(py_row.deserialize_field(blob, "tags"), ["hello", "世界"])

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
        fields = [
            ("label", "int64"),
            ("vector", "list_float32"),
            ("sparse_raw_terms", "list_string"),
            ("sparse_values", "list_float32"),
            ("fields", "string"),
            ("expire_ns_ts", "int64"),
            ("is_deleted", "boolean"),
        ]
        self.cpp_row = engine.BytesRow(
            engine.Schema(
                [
                    {"name": name, "data_type": getattr(engine.FieldType, dtype), "id": i}
                    for i, (name, dtype) in enumerate(fields)
                ]
            )
        )
        self.py_row = _PyBytesRow(
            _PySchema(
                [
                    {"name": name, "data_type": getattr(_PyFieldType, dtype), "id": i}
                    for i, (name, dtype) in enumerate(fields)
                ]
            )
        )

    def test_python_and_native_rows_share_one_binary_format(self):
        data = {
            "label": 2**40 + 17,
            "vector": [0.125, -0.875, 0.5],
            "sparse_raw_terms": ["a", "向量"],
            "sparse_values": [0.375, 1.25],
            "fields": '{"key": "value"}',
            "expire_ns_ts": 1234567890,
            "is_deleted": True,
        }
        py_bytes = self.py_row.serialize(data)
        cpp_bytes = self.cpp_row.serialize(data)

        self.assertEqual(py_bytes, cpp_bytes)
        self.assertEqual(self.cpp_row.deserialize(py_bytes), data)
        self.assertEqual(self.py_row.deserialize(cpp_bytes), data)


class TestTextFieldType(unittest.TestCase):
    """The `text` field type mirrors STRING but uses a uint32 length prefix,
    so it supports values larger than the 65535-byte STRING limit."""

    def setUp(self):
        self.cpp_fields = [
            {"name": "label", "data_type": engine.FieldType.int64, "id": 0},
            {"name": "body", "data_type": engine.FieldType.text, "id": 1},
        ]
        self.cpp_schema = engine.Schema(self.cpp_fields)
        self.cpp_row = engine.BytesRow(self.cpp_schema)

        self.py_fields = [
            {"name": "label", "data_type": _PyFieldType.int64, "id": 0},
            {"name": "body", "data_type": _PyFieldType.text, "id": 1},
        ]
        self.py_schema = _PySchema(self.py_fields)
        self.py_row = _PyBytesRow(self.py_schema)

    def _make_data(self):
        # Well beyond the 65535-byte STRING limit, plus multibyte content.
        return {"label": 42, "body": ("你好x" * 40000)}

    def test_large_unicode_text_is_readable_by_both_codecs(self):
        data = self._make_data()
        py_bytes = self.py_row.serialize(data)
        cpp_bytes = self.cpp_row.serialize(data)
        self.assertEqual(py_bytes, cpp_bytes)
        for row, encoded in ((self.cpp_row, py_bytes), (self.py_row, cpp_bytes)):
            self.assertEqual(row.deserialize_field(encoded, "body"), data["body"])
            self.assertEqual(row.deserialize_field(encoded, "label"), data["label"])

    def test_upgraded_text_schema_reads_legacy_rows_and_rejects_bad_versions(self):
        legacy_data = {"label": 42, "body": "升级前的记录"}
        readers = []
        for fields, field_type, schema, row in (
            (self.cpp_fields, engine.FieldType, engine.Schema, engine.BytesRow),
            (self.py_fields, _PyFieldType, _PySchema, _PyBytesRow),
        ):
            legacy_schema = schema([fields[0], {**fields[1], "data_type": field_type.string}])
            legacy_bytes = row(legacy_schema).serialize(legacy_data)
            reader = row(schema([fields[0], {**fields[1], "legacy_data_type": field_type.string}]))
            self.assertEqual(reader.deserialize(legacy_bytes), legacy_data)
            readers.append(reader)

        data = self._make_data()
        cpp_bytes, py_bytes = [reader.serialize(data) for reader in readers]
        self.assertEqual(cpp_bytes, py_bytes, "Binary content mismatch")
        for reader in readers:
            self.assertEqual(reader.deserialize(cpp_bytes), data)
            self.assertEqual(reader.deserialize_field(cpp_bytes, "label"), 42)
            for invalid in (b"\x00\x01", b"\x00\x02\x02"):
                with self.assertRaisesRegex((ValueError, RuntimeError), "record version"):
                    reader.deserialize(invalid)

    def test_text_declared_via_metadata(self):
        @serializable
        @dataclass
        class TextData:
            body: str = field(default="", metadata={"field_type": FieldType.text})

        text = "y" * 70000
        data = TextData(body=text)
        serialized = data.serialize()
        self.assertEqual(TextData.from_bytes(serialized).body, text)


if __name__ == "__main__":
    unittest.main()
