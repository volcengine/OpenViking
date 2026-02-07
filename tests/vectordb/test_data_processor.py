# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import shutil
import unittest
from datetime import datetime, timezone

from pydantic import ValidationError

from openviking.storage.vectordb.utils.data_processor import DataProcessor

DB_PATH = "./db_test_data_processor/"


def clean_dir(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)


class TestDataProcessor(unittest.TestCase):
    def setUp(self):
        self.fields_dict = {
            "created_at": {"FieldType": "date_time"},
            "geo": {"FieldType": "geo_point"},
            "uri": {"FieldType": "path"},
            "tags": {"FieldType": "list<string>"},
        }
        self.processor = DataProcessor(self.fields_dict)

    def test_scalar_index_meta_mapping(self):
        scalar_meta = self.processor.build_scalar_index_meta(["created_at", "geo", "uri", "tags"])
        mapped = {(item["FieldName"], item["FieldType"]) for item in scalar_meta}
        self.assertIn(("created_at", "int64"), mapped)
        self.assertIn(("geo_lon", "float32"), mapped)
        self.assertIn(("geo_lat", "float32"), mapped)
        self.assertIn(("uri", "path"), mapped)
        self.assertIn(("tags", "string"), mapped)

    def test_datetime_and_geo_point_conversion(self):
        data = {
            "created_at": "2026-02-06T12:34:56+00:00",
            "geo": "116.412138,39.914912",
            "tags": ["a", "b"],
        }
        converted = self.processor.convert_fields_dict_for_index(data)
        self.assertIsInstance(converted["created_at"], int)
        self.assertNotIn("geo", converted)
        self.assertIn("geo_lon", converted)
        self.assertIn("geo_lat", converted)
        self.assertEqual(converted["tags"], ["a", "b"])

    def test_filter_conversion_time_range(self):
        filters = {
            "op": "time_range",
            "field": "created_at",
            "gte": "2026-02-06T12:34:56+00:00",
        }
        converted = self.processor.convert_filter_for_index(filters)
        expected = int(
            datetime.fromisoformat("2026-02-06T12:34:56+00:00").astimezone(timezone.utc).timestamp()
            * 1000
        )
        self.assertEqual(converted["gte"], expected)

    def test_filter_conversion_geo_range(self):
        filters = {
            "op": "geo_range",
            "field": "geo",
            "center": "116.412138,39.914912",
            "radius": "10km",
        }
        converted = self.processor.convert_filter_for_index(filters)
        self.assertEqual(converted["field"], ["geo_lon", "geo_lat"])
        # Radius is converted to degrees: 10000m / 111320.0
        self.assertAlmostEqual(converted["radius"], 10000.0 / 111320.0, places=6)
        self.assertAlmostEqual(converted["center"][0], 116.412138, places=6)
        self.assertAlmostEqual(converted["center"][1], 39.914912, places=6)

    def test_validate_and_process(self):
        # Test basic validation
        data = {
            "created_at": "2026-02-06T12:34:56+00:00",
            "geo": "116.412138,39.914912",
            "tags": ["a", "b"],
            "uri": "/tmp/test",
        }
        processed = self.processor.validate_and_process(data)
        self.assertEqual(processed["tags"], ["a", "b"])

        # Test string input for list (legacy support)
        data_legacy = {
            "created_at": "2026-02-06T12:34:56+00:00",
            "geo": "116.412138,39.914912",
            "tags": "a;b;c",
            "uri": "/tmp/test",
        }
        processed_legacy = self.processor.validate_and_process(data_legacy)
        self.assertEqual(processed_legacy["tags"], ["a", "b", "c"])

        # Test invalid datetime
        data_invalid_dt = {
            "created_at": "invalid-date",
            "geo": "116.412138,39.914912",
            "tags": ["a"],
            "uri": "/tmp/test",
        }
        with self.assertRaises(ValidationError):
            self.processor.validate_and_process(data_invalid_dt)


if __name__ == "__main__":
    unittest.main()
