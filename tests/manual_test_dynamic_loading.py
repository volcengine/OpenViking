
import unittest
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
        # MockJoiner is in tests/mock_joiner.py, so we need to make sure tests module is importable
        # or use a path that python can find.
        # Assuming tests package is available or we use relative import if possible, 
        # but dynamic loader uses importlib.import_module which needs module path.
        
        # We'll use the MockJoiner we just created.
        # Since 'tests' might not be a package in installed environment, but here we are in source.
        # We might need to adjust python path or assume tests is importable.
        import sys
        import os
        sys.path.append(os.getcwd())

        config = {
            "Host": "test_host", 
            "Headers": {"Auth": "Token"},
            "CollectionClass": "tests.mock_joiner.MockJoiner",
            "CollectionArgs": {
                "custom_param1": "custom_val",
                "custom_param2": 123
            }
        }
        project = get_or_create_vikingdb_project(config=config)
        
        from tests.mock_joiner import MockJoiner
        self.assertEqual(project.CollectionClass, MockJoiner)
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
        
        print("Explicit loading test passed (MockJoiner with custom params)")

    def test_kwargs_loading(self):
        # Test with CollectionArgs
        config = {
            "Host": "test_host", 
            "CollectionClass": "tests.mock_joiner.MockJoiner",
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
