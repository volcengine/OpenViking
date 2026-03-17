#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Resource Watch Feature Example

This example demonstrates how to use the resource watch feature in OpenViking.
The watch feature allows you to automatically monitor and update resources at
specified intervals.

Key features:
- Create resources with watch enabled
- Query watch status
- Update watch intervals
- Cancel watch tasks
- Handle conflict errors

Usage:
    python watch_resource_example.py
"""

import asyncio
from pathlib import Path

from openviking import AsyncOpenViking
from openviking_cli.exceptions import ConflictError


async def example_basic_watch():
    """
    Example 1: Basic usage - Add resource and enable watch

    This shows how to add a resource with watch_interval parameter.
    When watch_interval > 0, a watch task is created and the resource
    will be automatically re-processed at the specified interval.
    """
    print("\n" + "=" * 60)
    print("Example 1: Basic Watch Usage")
    print("=" * 60)

    client = AsyncOpenViking(path="./data_watch_example")
    await client.initialize()

    try:
        test_file = Path("./test_resource.md")
        test_file.write_text(
            """# Test Resource

## Content
This is a test resource for watch functionality.

## Version
Version: 1.0
"""
        )

        to_uri = "viking://resources/watched_resource"

        print("\nAdding resource with watch_interval=60.0 minutes...")
        result = await client.add_resource(
            path=str(test_file),
            to=to_uri,
            reason="Example: monitoring a document",
            instruction="Check for updates and re-index",
            watch_interval=60.0,
        )

        print("Resource added successfully!")
        print(f"  Root URI: {result['root_uri']}")

        status = await client.get_watch_status(to_uri)
        if status:
            print("\nWatch Status:")
            print(f"  Is Watched: {status['is_watched']}")
            print(f"  Watch Interval: {status['watch_interval']} minutes")
            print(f"  Task ID: {status['task_id']}")
            print(f"  Next Execution: {status['next_execution_time']}")
            print(f"  Last Execution: {status['last_execution_time']}")

    finally:
        await client.close()


async def example_query_watch_status():
    """
    Example 2: Query watch status

    This shows how to check if a resource is being watched and
    get detailed information about the watch task.
    """
    print("\n" + "=" * 60)
    print("Example 2: Query Watch Status")
    print("=" * 60)

    client = AsyncOpenViking(path="./data_watch_example")
    await client.initialize()

    try:
        watched_uri = "viking://resources/watched_resource"
        unwatched_uri = "viking://resources/unwatched_resource"

        print(f"\nQuerying watch status for: {watched_uri}")
        status = await client.get_watch_status(watched_uri)

        if status:
            print("  Status: WATCHED")
            print(f"  Interval: {status['watch_interval']} minutes")
            print(f"  Active: {status['is_watched']}")
        else:
            print("  Status: NOT WATCHED")

        print(f"\nQuerying watch status for: {unwatched_uri}")
        status = await client.get_watch_status(unwatched_uri)

        if status:
            print("  Status: WATCHED")
            print(f"  Interval: {status['watch_interval']} minutes")
        else:
            print("  Status: NOT WATCHED")

    finally:
        await client.close()


async def example_update_watch_interval():
    """
    Example 3: Update watch interval

    This shows how to change the watch interval for an existing resource.
    Note: You need to first deactivate the watch task before updating it.
    """
    print("\n" + "=" * 60)
    print("Example 3: Update Watch Interval")
    print("=" * 60)

    client = AsyncOpenViking(path="./data_watch_example")
    await client.initialize()

    try:
        test_file = Path("./test_resource.md")
        to_uri = "viking://resources/watched_resource"

        print("\nCurrent watch status:")
        status = await client.get_watch_status(to_uri)
        if status:
            print(f"  Interval: {status['watch_interval']} minutes")
            task_id = status["task_id"]

            print("\nDeactivating watch task...")
            await client._service.resources._watch_manager.update_task(
                task_id=task_id,
                account_id=client._service.resources._ctx.account_id,
                user_id=client._service.resources._ctx.user.user_id,
                role=client._service.resources._ctx.role.value,
                is_active=False,
            )

            print("Updating watch interval to 120.0 minutes...")
            await client.add_resource(
                path=str(test_file),
                to=to_uri,
                reason="Updated: more frequent monitoring",
                watch_interval=120.0,
            )

            print("\nNew watch status:")
            status = await client.get_watch_status(to_uri)
            if status:
                print(f"  Interval: {status['watch_interval']} minutes")
                print(f"  Task ID: {status['task_id']} (same as before)")
        else:
            print("  Resource is not being watched")

    finally:
        await client.close()


async def example_cancel_watch():
    """
    Example 4: Cancel watch

    This shows how to cancel a watch task by setting watch_interval to 0
    or a negative value.
    """
    print("\n" + "=" * 60)
    print("Example 4: Cancel Watch")
    print("=" * 60)

    client = AsyncOpenViking(path="./data_watch_example")
    await client.initialize()

    try:
        test_file = Path("./test_resource.md")
        to_uri = "viking://resources/watched_resource"

        print("\nCurrent watch status:")
        status = await client.get_watch_status(to_uri)
        if status:
            print(f"  Is Watched: {status['is_watched']}")

            print("\nCancelling watch by setting interval to 0...")
            await client.add_resource(
                path=str(test_file),
                to=to_uri,
                watch_interval=0,
            )

            print("\nNew watch status:")
            status = await client.get_watch_status(to_uri)
            if status:
                print(f"  Is Watched: {status['is_watched']}")
            else:
                print("  Resource is no longer being watched")

        else:
            print("  Resource is not being watched")

    finally:
        await client.close()


async def example_handle_conflict():
    """
    Example 5: Handle conflict errors

    This shows how to handle ConflictError when trying to watch
    a resource that is already being watched by another task.
    """
    print("\n" + "=" * 60)
    print("Example 5: Handle Conflict Errors")
    print("=" * 60)

    client = AsyncOpenViking(path="./data_watch_example")
    await client.initialize()

    try:
        test_file = Path("./test_resource.md")
        to_uri = "viking://resources/conflict_example"

        print("\nCreating first watch task...")
        await client.add_resource(
            path=str(test_file),
            to=to_uri,
            watch_interval=30.0,
        )
        print("  First watch task created successfully")

        print("\nAttempting to create second watch task for same URI...")
        try:
            await client.add_resource(
                path=str(test_file),
                to=to_uri,
                watch_interval=60.0,
            )
            print("  ERROR: This should not happen!")

        except ConflictError as e:
            print("  ConflictError caught as expected!")
            print(f"  Error message: {e}")

            print("\nProper way to update: cancel first, then create new...")
            await client.add_resource(
                path=str(test_file),
                to=to_uri,
                watch_interval=0,
            )

            await client.add_resource(
                path=str(test_file),
                to=to_uri,
                watch_interval=60.0,
            )
            print("  Watch task updated successfully!")

    finally:
        await client.close()


async def example_multiple_resources():
    """
    Example 6: Watch multiple resources

    This shows how to manage multiple resources with different watch intervals.
    """
    print("\n" + "=" * 60)
    print("Example 6: Watch Multiple Resources")
    print("=" * 60)

    client = AsyncOpenViking(path="./data_watch_example")
    await client.initialize()

    try:
        resources = [
            ("./resource1.md", "viking://resources/resource1", 30.0),
            ("./resource2.md", "viking://resources/resource2", 60.0),
            ("./resource3.md", "viking://resources/resource3", 120.0),
        ]

        print("\nCreating multiple watched resources...")
        for path, uri, interval in resources:
            Path(path).write_text(f"# {path}\n\nContent for {path}")
            await client.add_resource(
                path=path,
                to=uri,
                reason=f"Monitoring {path}",
                watch_interval=interval,
            )
            print(f"  Created: {uri} (interval: {interval} min)")

        print("\nQuerying all watch statuses...")
        for _, uri, _ in resources:
            status = await client.get_watch_status(uri)
            if status:
                print(f"  {uri}:")
                print(f"    Interval: {status['watch_interval']} min")
                print(f"    Active: {status['is_watched']}")

        print("\nCancelling all watches...")
        for _, uri, _ in resources:
            await client.add_resource(
                path=resources[0][0],
                to=uri,
                watch_interval=0,
            )
            print(f"  Cancelled: {uri}")

    finally:
        await client.close()


async def example_reactivate_watch():
    """
    Example 7: Reactivate a cancelled watch

    This shows how to reactivate a watch task that was previously cancelled.
    """
    print("\n" + "=" * 60)
    print("Example 7: Reactivate Cancelled Watch")
    print("=" * 60)

    client = AsyncOpenViking(path="./data_watch_example")
    await client.initialize()

    try:
        test_file = Path("./test_resource.md")
        to_uri = "viking://resources/reactivate_example"

        print("\nCreating watch task...")
        await client.add_resource(
            path=str(test_file),
            to=to_uri,
            watch_interval=30.0,
        )

        status = await client.get_watch_status(to_uri)
        original_task_id = status["task_id"] if status else None
        print(f"  Task ID: {original_task_id}")

        print("\nCancelling watch...")
        await client.add_resource(
            path=str(test_file),
            to=to_uri,
            watch_interval=0,
        )

        status = await client.get_watch_status(to_uri)
        print(f"  Status after cancel: {'WATCHED' if status else 'NOT WATCHED'}")

        print("\nReactivating watch...")
        await client.add_resource(
            path=str(test_file),
            to=to_uri,
            reason="Reactivated monitoring",
            watch_interval=45.0,
        )

        status = await client.get_watch_status(to_uri)
        if status:
            print(f"  Task ID: {status['task_id']} (same as original)")
            print(f"  Interval: {status['watch_interval']} min")
            print(f"  Active: {status['is_watched']}")

    finally:
        await client.close()


async def main():
    """Run all examples."""
    print("\n" + "=" * 60)
    print("OpenViking Resource Watch Examples")
    print("=" * 60)

    await example_basic_watch()
    await example_query_watch_status()
    await example_update_watch_interval()
    await example_cancel_watch()
    await example_handle_conflict()
    await example_multiple_resources()
    await example_reactivate_watch()

    print("\n" + "=" * 60)
    print("All examples completed!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
