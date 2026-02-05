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
        print(f"Observer service: {client.observer}")

        # Test QueueObserver
        print("\n2. Test QueueObserver:")
        queue_status = client.observer.queue
        print(f"Type: {type(queue_status)}")
        print(f"Is healthy: {queue_status.is_healthy}")
        print(f"Has errors: {queue_status.has_errors}")

        # Test direct print
        print("\n3. Test direct print QueueObserver:")
        print(queue_status)

        # Test VikingDBObserver
        print("\n4. Test VikingDBObserver:")
        vikingdb_status = client.observer.vikingdb
        print(f"Type: {type(vikingdb_status)}")
        print(f"Is healthy: {vikingdb_status.is_healthy}")
        print(f"Has errors: {vikingdb_status.has_errors}")

        # Test direct print
        print("\n5. Test direct print VikingDBObserver:")
        print(vikingdb_status)

        # Test status string
        print("\n6. Test status string:")
        print(f"Status type: {type(vikingdb_status.status)}")
        print(f"Status length: {len(vikingdb_status.status)}")

        # Test system status
        print("\n7. Test system status:")
        system_status = client.observer.system
        print(f"System is_healthy: {system_status.is_healthy}")
        for name, component in system_status.components.items():
            print(f"\n{name}:")
            print(f"  is_healthy: {component.is_healthy}")
            print(f"  has_errors: {component.has_errors}")
            print(f"  status: {component.status[:100]}...")

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

    client = ov.OpenViking(path="./test_data")

    try:
        # Initialize
        client.initialize()
        print("Sync client initialized successfully")

        # Test observer access
        print(f"Observer service: {client.observer}")

        # Test QueueObserver
        print("\nQueueObserver status:")
        print(client.observer.queue)

        # Test VikingDBObserver
        print("\nVikingDBObserver status:")
        print(client.observer.vikingdb)

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
