# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import multiprocessing
import os
import shutil
import sys
import time
import unittest

from openviking.storage.vectordb.collection.local_collection import get_or_create_local_collection

DB_PATH_CRASH = "./test_db_crash_recovery"
DB_PATH_ROBUST = "./test_db_robust_crash"


def worker_write_and_crash(path, start_id, count, event_ready):
    """
    Subprocess function:
    1. Opens/Creates collection
    2. Writes data
    3. Signals readiness
    4. Waits to be killed (simulating crash without close())
    """
    try:
        # Setup collection
        meta_data = {
            "CollectionName": "crash_test_col",
            "Fields": [
                {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
                {"FieldName": "data", "FieldType": "string"},
            ],
        }
        # Force a fresh instance in this process
        col = get_or_create_local_collection(meta_data=meta_data, path=path)

        if not col.has_index("idx_crash"):
            col.create_index(
                "idx_crash",
                {"IndexName": "idx_crash", "VectorIndex": {"IndexType": "flat", "Distance": "l2"}},
            )

        # Write data
        data = []
        for i in range(count):
            uid = start_id + i
            data.append(
                {
                    "id": uid,
                    "vector": [0.1] * 4,  # Use constant vector for easier search verification
                    "data": f"crash_data_{uid}",
                }
            )

        print(f"[Subprocess] Upserting {count} items...")
        col.upsert_data(data)
        print("[Subprocess] Upsert done. Not closing.")

        # Notify main process that write is done
        event_ready.set()

        # Simulate work or wait to be killed
        # DO NOT close collection
        # We sleep long enough for parent to kill us
        time.sleep(60)
    except Exception as e:
        print(f"[Subprocess] Error: {e}")
        sys.exit(1)


def setup_robust_collection(path):
    """Helper to setup collection config for robust test"""
    meta_data = {
        "CollectionName": "robust_crash_col",
        "Fields": [
            {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
            {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
            {"FieldName": "data", "FieldType": "string"},
            {"FieldName": "tag", "FieldType": "string"},
        ],
    }
    col = get_or_create_local_collection(meta_data=meta_data, path=path)
    if not col.has_index("idx_main"):
        col.create_index(
            "idx_main",
            {"IndexName": "idx_main", "VectorIndex": {"IndexType": "flat", "Distance": "l2"}},
        )
    return col


def worker_cycle_1_write(path, event_ready):
    """
    Cycle 1: Write 500 items (ID 0-499).
    Crash immediately after.
    """
    try:
        col = setup_robust_collection(path)

        data = []
        for i in range(500):
            data.append({"id": i, "vector": [0.1] * 4, "data": f"cycle1_{i}", "tag": "c1"})

        print("[Cycle 1] Upserting 500 items...")
        col.upsert_data(data)
        print("[Cycle 1] Upsert done.")

        event_ready.set()
        time.sleep(60)  # Wait to be killed
    except Exception as e:
        print(f"[Cycle 1] Error: {e}")
        sys.exit(1)


def worker_cycle_2_mix(path, event_ready):
    """
    Cycle 2:
    - Recover collection (happens automatically on open)
    - Delete first 100 items (ID 0-99)
    - Write 300 new items (ID 1000-1299)
    Crash immediately after.
    """
    try:
        col = setup_robust_collection(path)

        # Verify Cycle 1 data exists before modification
        res = col.fetch_data([0, 499])
        if len(res.items) != 2:
            print("[Cycle 2] Critical: Cycle 1 data missing on startup!")
            sys.exit(1)

        # Delete 0-99
        print("[Cycle 2] Deleting 100 items (0-99)...")
        col.delete_data(list(range(100)))

        # Upsert 1000-1299
        print("[Cycle 2] Upserting 300 items (1000-1299)...")
        data = []
        for i in range(300):
            uid = 1000 + i
            data.append({"id": uid, "vector": [0.2] * 4, "data": f"cycle2_{uid}", "tag": "c2"})
        col.upsert_data(data)
        print("[Cycle 2] Operations done.")

        event_ready.set()
        time.sleep(60)  # Wait to be killed
    except Exception as e:
        print(f"[Cycle 2] Error: {e}")
        sys.exit(1)


class TestCrashRecovery(unittest.TestCase):
    def setUp(self):
        if os.path.exists(DB_PATH_CRASH):
            shutil.rmtree(DB_PATH_CRASH)
        if os.path.exists(DB_PATH_ROBUST):
            shutil.rmtree(DB_PATH_ROBUST)

    def tearDown(self):
        if os.path.exists(DB_PATH_CRASH):
            shutil.rmtree(DB_PATH_CRASH)
        if os.path.exists(DB_PATH_ROBUST):
            shutil.rmtree(DB_PATH_ROBUST)

    def test_simple_crash_recovery(self):
        """
        Test that data and indexes are recovered correctly after a process crash
        (where close() was not called).
        """
        print("\n=== Test Simple Crash Recovery ===")

        # Use 'spawn' to ensure clean process state (especially important for RocksDB/LevelDB locks)
        ctx = multiprocessing.get_context("spawn")
        event = ctx.Event()

        data_count = 100
        p = ctx.Process(target=worker_write_and_crash, args=(DB_PATH_CRASH, 0, data_count, event))
        p.start()

        # Wait for write to complete in subprocess
        print("[Main] Waiting for subprocess to write data...")
        is_set = event.wait(timeout=30)
        self.assertTrue(is_set, "Subprocess timed out writing data")

        # Give it a tiny moment to ensure the OS flush might happen (though we want to test robustness)
        # But immediate kill is what we want to test.
        # However, Python's LevelDB binding might be sync or async. Usually writes go to OS cache.
        time.sleep(0.5)

        # KILL the process immediately to simulate crash (no cleanup, no flush)
        print("[Main] Terminating subprocess (Simulating Crash)...")
        p.terminate()
        p.join()
        print(f"[Main] Subprocess terminated with exit code: {p.exitcode}")

        # Recover
        print("[Main] Recovering collection in main process...")
        # Re-opening the collection should trigger recovery logic (PersistentIndex should rebuild/catchup from Store)
        col = get_or_create_local_collection(path=DB_PATH_CRASH)

        # 1. Verify Data Persistence (Store)
        print("[Main] Verifying data fetch (All 100 items)...")
        # Check all IDs
        all_ids = list(range(data_count))
        res = col.fetch_data(all_ids)

        print(f"[Main] Fetch retrieved {len(res.items)} items. Missing: {len(res.ids_not_exist)}")

        self.assertEqual(
            len(res.items), data_count, f"Should find all {data_count} items in KV Store"
        )

        print("[Main] Data fetch verified.")

        # 2. Verify Index Recovery
        # Since we killed the process, the Index (which might be in memory or partially written)
        # needs to be reconstructed from the KV Store delta during initialization.
        print("[Main] Verifying vector search (Index Recovery)...")

        # Searching for vector [0.1, 0.1, 0.1, 0.1] should return our data
        search_res = col.search_by_vector("idx_crash", dense_vector=[0.1] * 4, limit=data_count)

        found_ids = [item.id for item in search_res.data]
        print(f"[Main] Search returned {len(found_ids)} items.")

        # We expect all items to be found if index recovery works
        self.assertEqual(
            len(found_ids), data_count, "Index should contain all items after recovery"
        )

        col.close()
        print("[Main] Simple recovery test passed.")

    def run_process_and_crash(self, target_func):
        ctx = multiprocessing.get_context("spawn")
        event = ctx.Event()
        p = ctx.Process(target=target_func, args=(DB_PATH_ROBUST, event))
        p.start()

        # Wait for work done
        is_set = event.wait(timeout=30)
        self.assertTrue(is_set, "Subprocess timed out")

        # Give a split second for OS buffers (simulate sudden power loss/crash)
        time.sleep(0.5)

        print(f"[Main] Crashing process {p.pid}...")
        p.terminate()
        p.join()
        print(f"[Main] Process {p.pid} crashed.")

    def test_multi_cycle_crash(self):
        print("\n=== Test Robust Multi-Cycle Crash Recovery ===")

        # --- Cycle 1: Write & Crash ---
        print("\n--- Starting Cycle 1 ---")
        self.run_process_and_crash(worker_cycle_1_write)

        # Verify Cycle 1 Recovery
        print("[Main] Verifying Cycle 1 recovery...")
        col = setup_robust_collection(DB_PATH_ROBUST)

        # Check counts
        res_search = col.search_by_vector("idx_main", [0.1] * 4, limit=1000)
        self.assertEqual(len(res_search.data), 500, "Should have 500 items after Cycle 1")
        col.close()

        # --- Cycle 2: Mix Ops & Crash ---
        print("\n--- Starting Cycle 2 ---")
        self.run_process_and_crash(worker_cycle_2_mix)

        # Verify Cycle 2 Recovery
        print("[Main] Verifying Cycle 2 recovery...")
        col = setup_robust_collection(DB_PATH_ROBUST)

        # 1. Verify Deletions (0-99 should be gone)
        print("[Main] Verifying deletions (0-99)...")
        res_deleted = col.fetch_data(list(range(10)))
        self.assertEqual(len(res_deleted.items), 0, "IDs 0-9 should be deleted")

        # Search check for deleted items
        # IDs 0-99 had vector [0.1]*4.
        # IDs 100-499 still have [0.1]*4.
        # So searching [0.1]*4 should return 400 items that match perfectly (score ~0)
        # But vector search returns all items up to limit.
        res_search_old = col.search_by_vector("idx_main", [0.1] * 4, limit=1000)

        # Verify total count is 700 (400 old + 300 new)
        self.assertEqual(len(res_search_old.data), 700, "Total index items should be 700")

        # Verify IDs 0-99 are GONE
        found_ids = {item.id for item in res_search_old.data}
        for i in range(100):
            self.assertNotIn(i, found_ids, f"ID {i} should have been deleted")

        # Verify IDs 100-499 EXIST
        for i in range(100, 500):
            self.assertIn(i, found_ids, f"ID {i} should exist")

        # 2. Verify New Inserts (1000-1299 should exist)
        print("[Main] Verifying new inserts (1000-1299)...")
        res_new = col.fetch_data([1000, 1299])
        self.assertEqual(len(res_new.items), 2, "New items 1000 and 1299 should exist")

        # Search check for new items ([0.2]*4)
        # Should also return all 700 items, but sorted differently
        res_search_new = col.search_by_vector("idx_main", [0.2] * 4, limit=1000)
        self.assertEqual(len(res_search_new.data), 700, "Should return all 700 items")

        # Verify IDs 1000-1299 EXIST in the result
        found_ids_new = {item.id for item in res_search_new.data}
        for i in range(1000, 1300):
            self.assertIn(i, found_ids_new, f"ID {i} should exist")

        print("[Main] Total items verified via index: 700")

        col.close()
        print("\n[Main] Robust recovery test passed.")


if __name__ == "__main__":
    unittest.main()
