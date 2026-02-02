# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import unittest
from dataclasses import dataclass, field
from typing import List

from openviking.storage.vectordb.store.bytes_row import FieldType
from openviking.storage.vectordb.store.serializable import serializable


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


if __name__ == "__main__":
    unittest.main()
