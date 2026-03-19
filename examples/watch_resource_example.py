#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Resource Watch Feature Example

This example demonstrates how to use the resource watch feature in OpenViking.
The watch feature allows you to automatically re-process resources at specified
intervals.

Key features:
- Create resources with watch enabled
- Update watch intervals (cancel then re-create)
- Cancel watch tasks
- Handle conflict errors
"""

import asyncio
from pathlib import Path

from openviking import AsyncOpenViking
from openviking_cli.exceptions import ConflictError


async def example_basic_watch():
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
    finally:
        await client.close()


async def example_update_watch_interval():
    client = AsyncOpenViking(path="./data_watch_example")
    await client.initialize()

    try:
        test_file = Path("./test_resource.md")
        to_uri = "viking://resources/watched_resource"

        print("\nUpdating watch interval by canceling then re-creating...")
        await client.add_resource(
            path=str(test_file),
            to=to_uri,
            watch_interval=0,
        )
        await client.add_resource(
            path=str(test_file),
            to=to_uri,
            reason="Updated: more frequent monitoring",
            watch_interval=120.0,
        )
        print("Watch task updated successfully!")
    finally:
        await client.close()


async def example_cancel_watch():
    client = AsyncOpenViking(path="./data_watch_example")
    await client.initialize()

    try:
        test_file = Path("./test_resource.md")
        to_uri = "viking://resources/watched_resource"

        print("\nCancelling watch by setting interval to 0...")
        await client.add_resource(
            path=str(test_file),
            to=to_uri,
            watch_interval=0,
        )
        print("Watch task cancelled successfully!")
    finally:
        await client.close()


async def example_handle_conflict():
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
    finally:
        await client.close()


async def main():
    print("\n" + "=" * 60)
    print("OpenViking Resource Watch Examples")
    print("=" * 60)

    await example_basic_watch()
    await example_update_watch_interval()
    await example_cancel_watch()
    await example_handle_conflict()

    print("\n" + "=" * 60)
    print("All examples completed!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())

