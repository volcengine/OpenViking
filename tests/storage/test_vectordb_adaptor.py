# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Add paths to sys.path to ensure modules can be found
# sys.path.insert(0, "/cloudide/workspace/viking_python_client")
sys.path.insert(0, "/cloudide/workspace/open_test")

from openviking.storage.vectordb_adapters.factory import create_collection_adapter
from tests.storage.mock_backend import MockCollectionAdapter

class TestAdapterLoading(unittest.TestCase):

    def test_dynamic_loading_mock_adapter(self):
        """
        Test that create_collection_adapter can dynamically load MockCollectionAdapter
        from tests.storage.mock_backend using the full class path string.
        """
        class MockConfig:
            def __init__(self):
                # Use MockCollectionAdapter from tests.storage.mock_backend
                self.backend = "tests.storage.mock_backend.MockCollectionAdapter"
                self.name = "mock_test_collection"
                self.custom_param1 = "val1"
                self.custom_param2 = 123

        config = MockConfig()

        try:
            adapter = create_collection_adapter(config)
            
            self.assertEqual(adapter.__class__.__name__, "MockCollectionAdapter")
            self.assertEqual(adapter.mode, "mock")
            self.assertEqual(adapter.collection_name, "mock_test_collection")
            self.assertEqual(adapter.custom_param1, "val1")
            self.assertEqual(adapter.custom_param2, 123)
            
            # Verify internal behavior
            exists = adapter.collection_exists()
            self.assertTrue(exists)
            
            print("Successfully loaded MockCollectionAdapter dynamically.")
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.fail(f"Failed to load adapter dynamically: {e}")

if __name__ == "__main__":
    unittest.main()