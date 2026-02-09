# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import unittest

from openviking.storage.vectordb.project.vikingdb_project import get_or_create_vikingdb_project


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

    def test_create_vikingdb_project(self):
        """Test project initialization."""
        project = get_or_create_vikingdb_project(self.project_name, self.config)
        self.assertEqual(project.project_name, self.project_name)
        self.assertEqual(project.host, self.config["Host"])
        self.assertEqual(project.headers, self.config["Headers"])

    def test_create_collection(self):
        """Test collection creation with custom headers."""
        project = get_or_create_vikingdb_project(self.project_name, self.config)
        meta_data = self.meta_data

        collection = project.create_collection("test_coll", meta_data)

        self.assertIsNotNone(collection)
        self.assertIn("test_coll", project.list_collections())

    def test_upsert_data(self):
        """Test data upsert with custom headers and path."""
        project = get_or_create_vikingdb_project(self.project_name, self.config)

        # Get existing or create new collection
        meta_data = self.meta_data
        collection = project.get_or_create_collection("test_coll", meta_data)

        data = [{"id": "1", "vector": [0.1] * 128, "text": "123"}]
        res = collection.upsert_data(data)
        self.assertIsNone(res)

    def test_fetch_data(self):
        """Test data fetching."""
        project = get_or_create_vikingdb_project(self.project_name, self.config)

        collection = project.get_or_create_collection("test_coll", self.meta_data)

        # Upsert some data first to fetch it
        data = [{"id": "1", "vector": [0.1] * 128, "text": "hello"}]
        collection.upsert_data(data)

        result = collection.fetch_data(["1"])

        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0].id, "1")
        self.assertEqual(result.items[0].fields["text"], "hello")

    def test_drop_collection(self):
        """Test collection dropping."""
        project = get_or_create_vikingdb_project(self.project_name, self.config)

        collection = project.get_or_create_collection("test_coll", self.meta_data)
        if not collection:
            self.fail("Collection should exist after creation")

        collection.drop()
        collection = project.get_collection("test_coll")
        self.assertIsNone(collection)


if __name__ == "__main__":
    unittest.main()
