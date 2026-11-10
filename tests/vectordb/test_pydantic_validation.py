# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import os
import sys
import unittest

from openviking.storage.vectordb.utils import validation

sys.path.append(os.getcwd())


class TestPydanticValidation(unittest.TestCase):
    def test_valid_collection_meta(self):
        meta = {
            "CollectionName": "test_collection",
            "Fields": [
                {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
                {"FieldName": "vec", "FieldType": "vector", "Dim": 128},
                {"FieldName": "desc", "FieldType": "string"},
            ],
        }
        self.assertTrue(validation.is_valid_collection_meta_data(meta))

    def test_invalid_collection_meta(self):
        # Missing required field
        meta = {"Fields": []}
        self.assertFalse(validation.is_valid_collection_meta_data(meta))

        # Invalid dim
        meta = {
            "CollectionName": "test",
            "Fields": [
                {"FieldName": "vec", "FieldType": "vector", "Dim": 129}
            ],  # Not multiple of 4
        }
        self.assertFalse(validation.is_valid_collection_meta_data(meta))

    def test_valid_index_meta(self):
        meta = {
            "IndexName": "test_index",
            "VectorIndex": {"IndexType": "flat", "Distance": "L2", "Quant": "float"},
        }
        fields_meta = {}
        self.assertTrue(validation.is_valid_index_meta_data(meta, fields_meta))

if __name__ == "__main__":
    unittest.main()
