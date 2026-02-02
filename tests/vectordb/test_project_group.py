# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import os
import shutil
import unittest

from openviking.storage.vectordb.project.project_group import get_or_create_project_group

TEST_PROJECT_ROOT = "./test_project_root"


class TestProjectGroup(unittest.TestCase):
    def setUp(self):
        if os.path.exists(TEST_PROJECT_ROOT):
            shutil.rmtree(TEST_PROJECT_ROOT)

    def tearDown(self):
        if os.path.exists(TEST_PROJECT_ROOT):
            shutil.rmtree(TEST_PROJECT_ROOT)

    def test_volatile_group(self):
        # Path empty -> Volatile
        group = get_or_create_project_group("")
        self.assertTrue(group.has_project("default"))

        # Create new
        p1 = group.create_project("p1")
        self.assertIsNotNone(p1)
        self.assertTrue(group.has_project("p1"))

        # List
        names = group.list_projects()
        self.assertIn("default", names)
        self.assertIn("p1", names)

        # Close
        group.close()

    def test_persistent_group_lifecycle(self):
        # 1. Create and populate
        group = get_or_create_project_group(TEST_PROJECT_ROOT)

        # Default should be created automatically
        self.assertTrue(group.has_project("default"))
        self.assertTrue(os.path.exists(os.path.join(TEST_PROJECT_ROOT, "default")))

        # Create persistent project
        group.create_project("analytics")
        self.assertTrue(os.path.exists(os.path.join(TEST_PROJECT_ROOT, "analytics")))

        # Close to flush/release
        group.close()

        # 2. Reload from disk
        group2 = get_or_create_project_group(TEST_PROJECT_ROOT)
        self.assertTrue(group2.has_project("default"))
        self.assertTrue(group2.has_project("analytics"))

        # 3. Delete project
        group2.delete_project("analytics")
        self.assertFalse(group2.has_project("analytics"))
        # Verify folder removed? Logic says it should drop collections, but maybe not the folder itself?
        # Let's check implementation. ProjectGroup.delete_project removes from dict and drops collections.
        # It calls `project.drop_collection` for all collections.
        # It does NOT explicitly delete the project directory in the `ProjectGroup` code I read.
        # Wait, let me check `LocalProject` code if `close()` or `drop()` handles it?
        # The `ProjectGroup.delete_project` code:
        # project.close()
        # It does not seem to remove the project directory itself in `project_group.py`.
        # However, for robustness, we at least ensure it's gone from memory.

        group2.close()

    def test_duplicate_create_error(self):
        group = get_or_create_project_group("")
        group.create_project("dup_test")
        with self.assertRaises(ValueError):
            group.create_project("dup_test")
        group.close()


if __name__ == "__main__":
    unittest.main()
