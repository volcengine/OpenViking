# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
import os
import sys
import unittest

# Add paths to sys.path to ensure modules can be found
# sys.path.insert(0, "/cloudide/workspace/viking_python_client")
sys.path.insert(0, "/cloudide/workspace/open_test")

import json
import shutil
import tempfile

from openviking.storage.vectordb_adapters.factory import create_collection_adapter
from openviking_cli.utils.config import OpenVikingConfigSingleton, get_openviking_config


class TestAdapterLoading(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.test_dir, "ov.conf")

        # Create a valid config file
        config_data = {
            "storage": {
                "vectordb": {
                    "backend": "tests.storage.mock_backend.MockCollectionAdapter",
                    "name": "mock_test_collection",
                    "index_name": "mock_test_index",
                    "custom_params": {"custom_param1": "val1", "custom_param2": 123},
                }
            },
            "embedding": {
                "dense": {
                    "provider": "openai",
                    "model": "text-embedding-3-small",
                    "api_key": "mock-key",
                    "dimension": 1536,
                }
            },
        }
        with open(self.config_path, "w") as f:
            json.dump(config_data, f)

    def tearDown(self):
        shutil.rmtree(self.test_dir)
        # Reset singleton to avoid side effects on other tests
        OpenVikingConfigSingleton.reset_instance()

    def test_dynamic_loading_mock_adapter(self):
        """
        Test that create_collection_adapter can dynamically load MockCollectionAdapter
        from tests.storage.mock_backend using the full class path string,
        loaded from a real configuration file.
        """
        # Load config from the temporary file
        OpenVikingConfigSingleton.initialize(config_path=self.config_path)

        config = get_openviking_config().storage.vectordb

        # Verify that custom params are loaded
        # Since we use custom_params dict
        self.assertEqual(config.custom_params.get("custom_param1"), "val1")
        self.assertEqual(config.custom_params.get("custom_param2"), 123)

        try:
            adapter = create_collection_adapter(config)

            self.assertEqual(adapter.__class__.__name__, "MockCollectionAdapter")
            self.assertEqual(adapter.mode, "mock")
            self.assertEqual(adapter.collection_name, "mock_test_collection")
            self.assertEqual(adapter.index_name, "mock_test_index")
            self.assertEqual(adapter.custom_param1, "val1")
            self.assertEqual(adapter.custom_param2, 123)

            # Verify internal behavior
            exists = adapter.collection_exists()
            self.assertTrue(exists)

            print("Successfully loaded MockCollectionAdapter dynamically from config file.")

        except Exception as e:
            import traceback

            traceback.print_exc()
            self.fail(f"Failed to load adapter dynamically: {e}")


if __name__ == "__main__":
    unittest.main()
