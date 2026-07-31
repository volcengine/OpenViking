# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
import unittest


@unittest.skip("Temporarily skip TestVikingDBProject")
class TestVikingDBProject(unittest.TestCase):
    """
    Unit tests for VikingDB Project and Collection implementation for private deployment.
    """

    def setUp(self):
        self.config = {
            "Host": "http://localhost:8080",
            "Headers": {
                "X-Top-Account-Id": "1",
                "X-Top-User-Id": "1000",
                "X-Top-IdentityName": "test-user",
                "X-Top-Role-Id": "data",
            },
        }
        self.project_name = "test_project"
        meta_data = {
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 128},
                {"FieldName": "text", "FieldType": "string"},
            ]
        }
        self.meta_data = meta_data

if __name__ == "__main__":
    unittest.main()
