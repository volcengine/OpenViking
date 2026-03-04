import unittest
import sys
import os

# Add open_test path to ensure modules can be found
sys.path.insert(0, "/cloudide/workspace/open_test")

from openviking.storage.vectordb.project.vikingdb_project import get_or_create_vikingdb_project, VikingDBProject
from openviking.storage.vectordb.collection.vikingdb_collection import VikingDBCollection

class TestDynamicLoading(unittest.TestCase):
    def test_default_loading(self):
        # Test with default configuration
        config = {"Host": "test_host"}
        project = get_or_create_vikingdb_project(config=config)
        self.assertEqual(project.CollectionClass, VikingDBCollection)
        print("Default loading test passed")

    def test_explicit_loading(self):
        # Test with explicit configuration pointing to MockJoiner
        # MockJoiner is now in tests.storage.mock_backend
        
        # We assume tests package structure is available from /cloudide/workspace/open_test
        
        config = {
            "Host": "test_host", 
            "Headers": {"Auth": "Token"},
            "CollectionClass": "tests.storage.mock_backend.MockCollection",
            "CollectionArgs": {
                "custom_param1": "custom_val",
                "custom_param2": 123
            }
        }
        project = get_or_create_vikingdb_project(config=config)
        
        from tests.storage.mock_backend import MockCollection
        self.assertEqual(project.CollectionClass, MockCollection)
        self.assertEqual(project.host, "test_host")
        self.assertEqual(project.headers, {"Auth": "Token"})
        self.assertEqual(project.collection_args, {"custom_param1": "custom_val", "custom_param2": 123})
        
        # Test collection creation to verify params are passed
        collection_name = "test_collection"
        meta_data = {
            "test_verification": True,
            "Host": "metadata_host",
            "Headers": {"Meta": "Header"}
        }
        
        # The project wrapper will pass host, headers, meta_data, AND collection_args
        kwargs = {
            "host": project.host,
            "headers": project.headers,
            "meta_data": meta_data
        }
        kwargs.update(project.collection_args)
        
        collection_instance = project.CollectionClass(**kwargs)
        
        # Verify custom params are set correctly
        self.assertEqual(collection_instance.custom_param1, "custom_val")
        self.assertEqual(collection_instance.custom_param2, 123)
        
        # Verify host/headers are in kwargs (since init doesn't take them explicitly anymore)
        self.assertEqual(collection_instance.kwargs.get("host"), "test_host")
        self.assertEqual(collection_instance.kwargs.get("headers"), {"Auth": "Token"})
        
        print("Explicit loading test passed (MockCollection with custom params)")

    def test_kwargs_loading(self):
        # Test with CollectionArgs
        config = {
            "Host": "test_host", 
            "CollectionClass": "tests.storage.mock_backend.MockCollection",
            "CollectionArgs": {
                "custom_param1": "extra_value",
                "custom_param2": 456
            }
        }
        project = get_or_create_vikingdb_project(config=config)
        
        self.assertEqual(project.collection_args, {"custom_param1": "extra_value", "custom_param2": 456})
        
        # Manually verify instantiation with kwargs
        kwargs = {
            "host": project.host,
            "headers": project.headers,
            "meta_data": {"test_verification": True}
        }
        kwargs.update(project.collection_args)
        
        collection_instance = project.CollectionClass(**kwargs)
        self.assertEqual(collection_instance.custom_param1, "extra_value")
        self.assertEqual(collection_instance.custom_param2, 456)
        print("Kwargs loading test passed")

    def test_invalid_loading(self):
        # Test with invalid class path
        config = {
            "Host": "test_host", 
            "CollectionClass": "non.existent.module.Class"
        }
        with self.assertRaises(ImportError):
            get_or_create_vikingdb_project(config=config)
        print("Invalid loading test passed")

if __name__ == '__main__':
    unittest.main()
