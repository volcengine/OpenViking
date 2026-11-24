# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
import unittest

from pydantic import ValidationError

from openviking.storage.vectordb.utils.data_processor import DataProcessor


class TestDataProcessor(unittest.TestCase):
    def setUp(self):
        self.fields_dict = {
            "created_at": {"FieldType": "date_time"},
            "geo": {"FieldType": "geo_point"},
            "uri": {"FieldType": "path"},
            "tags": {"FieldType": "list<string>"},
            "abstract": {"FieldType": "string"},
        }
        self.processor = DataProcessor(self.fields_dict)

    def test_index_schema_rows_and_filters_use_matching_time_and_geo_units(self):
        date = "2026-02-06T20:34:56+08:00"
        lon, lat = 116.412138, 39.914912
        epoch_ms = 1770381296000
        data = {"created_at": date, "geo": f"{lon},{lat}", "tags": ["a", "b"]}
        converted = self.processor.convert_fields_dict_for_index(data)
        schema = self.processor.build_scalar_index_meta(["created_at", "geo", "uri", "tags"])
        types = {item["FieldName"]: item["FieldType"] for item in schema}

        self.assertEqual(
            types,
            {
                "created_at": "int64",
                "geo_lon": "float32",
                "geo_lat": "float32",
                "uri": "path",
                "tags": "list<string>",
            },
        )
        self.assertEqual(
            converted,
            {"created_at": epoch_ms, "geo_lon": lon, "geo_lat": lat, "tags": ["a", "b"]},
        )

        time_filter = self.processor.convert_filter_for_index(
            {"op": "time_range", "field": "created_at", "gte": "2026-02-06T12:34:56+00:00"}
        )
        geo_filter = self.processor.convert_filter_for_index(
            {"op": "geo_range", "field": "geo", "center": f"{lon},{lat}", "radius": "10km"}
        )
        self.assertEqual(time_filter["gte"], epoch_ms)
        self.assertEqual(geo_filter["field"], ["geo_lon", "geo_lat"])
        self.assertEqual(geo_filter["center"], [lon, lat])
        self.assertAlmostEqual(geo_filter["radius"], 10_000 / 111_320.0, places=6)

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

    def test_validate_and_process_sanitizes_lone_surrogates(self):
        processed = self.processor.validate_and_process(
            {"abstract": "bad \ud800", "tags": ["ok \udc00"], "uri": "/tmp/test"}
        )

        self.assertEqual(processed["abstract"], "bad \ufffd")
        self.assertEqual(processed["tags"], ["ok \ufffd"])
        processed["abstract"].encode("utf-8")

    def test_index_conversion_repairs_legacy_surrogates_and_preserves_valid_emoji(self):
        fields_json = json.dumps(
            {"abstract": "legacy \ud800 face 😀", "tags": ["ok \udc00"], "uri": "/tmp/test"}
        )

        converted_json = self.processor.convert_fields_for_index(fields_json)
        converted_json.encode("utf-8")
        converted = json.loads(converted_json)

        self.assertEqual(converted["abstract"], "legacy \ufffd face 😀")
        self.assertEqual(converted["tags"], ["ok \ufffd"])

    def test_convert_fields_for_index_handles_corrupted_json(self):
        corrupted = '{"abstract": "truncated\x00\xff", "uri": "/tmp/bad"'

        result = self.processor.convert_fields_for_index(corrupted)

        self.assertEqual(result, corrupted)


if __name__ == "__main__":
    unittest.main()
