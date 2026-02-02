#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Test VikingDBObserver functionality
"""

import asyncio

import openviking as ov


async def test_vikingdb_observer():
    """Test VikingDBObserver functionality"""
    print("=== Test VikingDBObserver ===")

    # Create client
    client = ov.AsyncOpenViking(path="./test_data")

    try:
        # Initialize client
        await client.initialize()
        print("Client initialized successfully")

        # Test observer access
        print("\n1. Test observer access:")
        print(f"Available observers: {list(client.observers.keys())}")

        # Test QueueObserver
        print("\n2. Test QueueObserver:")
        queue_observer = client.observers["queue"]
        print(f"Type: {type(queue_observer)}")
        print(f"Is healthy: {queue_observer.is_healthy()}")
        print(f"Has errors: {queue_observer.has_errors()}")

        # Test direct print
        print("\n3. Test direct print QueueObserver:")
        print(queue_observer)

        # Test VikingDBObserver
        print("\n4. Test VikingDBObserver:")
        vikingdb_observer = client.observers["vikingdb"]
        print(f"Type: {type(vikingdb_observer)}")
        print(f"Is healthy: {vikingdb_observer.is_healthy()}")
        print(f"Has errors: {vikingdb_observer.has_errors()}")

        # Test direct print
        print("\n5. Test direct print VikingDBObserver:")
        print(vikingdb_observer)

        # Test get status table
        print("\n6. Test get status table:")
        status_table = vikingdb_observer.get_status_table()
        print(f"Status table type: {type(status_table)}")
        print(f"Status table length: {len(status_table)}")

        # Test observer properties
        print("\n7. Test observer properties:")
        for name, observer in client.observers.items():
            print(f"\n{name}:")
            print(f"  is_healthy: {observer.is_healthy()}")
            print(f"  has_errors: {observer.has_errors()}")
            print(f"  str(observer): {str(observer)[:100]}...")

        print("\n=== All tests completed ===")

    except Exception as e:
        print(f"Error during test: {e}")
        import traceback

        traceback.print_exc()

    finally:
        # Close client
        await client.close()
        print("Client closed")


def test_sync_client():
    """Test sync client"""
    print("\n=== Test sync client ===")

    client = ov.OpenViking(path="./test_data_sync")

    try:
        # Initialize
        client.initialize()
        print("Sync client initialized successfully")

        # Test observer access
        print(f"Available observers: {list(client.observers.keys())}")

        # Test QueueObserver
        print("\nQueueObserver status:")
        print(client.observers["queue"])

        # Test VikingDBObserver
        print("\nVikingDBObserver status:")
        print(client.observers["vikingdb"])

        print("\n=== Sync client test completed ===")

    except Exception as e:
        print(f"Sync client test error: {e}")
        import traceback

        traceback.print_exc()

    finally:
        client.close()
        print("Sync client closed")


if __name__ == "__main__":
    # Run async test
    asyncio.run(test_vikingdb_observer())

    # Run sync test
    test_sync_client()
