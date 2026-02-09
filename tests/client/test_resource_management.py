# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Resource management tests"""

from pathlib import Path

from openviking import AsyncOpenViking


class TestAddResource:
    """Test add_resource"""

    async def test_add_resource_success(self, client: AsyncOpenViking, sample_markdown_file: Path):
        """Test successful resource addition"""
        result = await client.add_resource(path=str(sample_markdown_file), reason="Test resource")

        assert "root_uri" in result
        assert result["root_uri"].startswith("viking://")

    async def test_add_resource_with_wait(
        self, client: AsyncOpenViking, sample_markdown_file: Path
    ):
        """Test adding resource and waiting for processing"""
        result = await client.add_resource(
            path=str(sample_markdown_file),
            reason="Test resource",
            wait=True,
        )

        print(result)
        assert "root_uri" in result
        assert "queue_status" in result

    async def test_add_resource_without_wait(
        self, client: AsyncOpenViking, sample_markdown_file: Path
    ):
        """Test adding resource without waiting (async mode)"""
        result = await client.add_resource(
            path=str(sample_markdown_file), reason="Test resource", wait=False
        )

        assert "root_uri" in result
        # In async mode, status can be monitored via observer
        observer = client.observer
        assert observer.queue is not None

    async def test_add_resource_with_target(
        self, client: AsyncOpenViking, sample_markdown_file: Path
    ):
        """Test adding resource to specified target"""
        result = await client.add_resource(
            path=str(sample_markdown_file),
            target="viking://resources/custom/",
            reason="Test resource",
        )

        assert "root_uri" in result
        assert "custom" in result["root_uri"]

    async def test_add_resource_file_not_found(self, client: AsyncOpenViking):
        """Test adding nonexistent file"""

        res = await client.add_resource(path="/nonexistent/file.txt", reason="Test")

        assert "errors" in res and len(res["errors"]) > 0


class TestWaitProcessed:
    """Test wait_processed"""

    async def test_wait_processed_success(
        self, client: AsyncOpenViking, sample_markdown_file: Path
    ):
        """Test waiting for processing to complete"""
        await client.add_resource(path=str(sample_markdown_file), reason="Test")

        status = await client.wait_processed()

        assert isinstance(status, dict)

    async def test_wait_processed_empty_queue(self, client: AsyncOpenViking):
        """Test waiting on empty queue"""
        status = await client.wait_processed()

        assert isinstance(status, dict)

    async def test_wait_processed_multiple_resources(
        self, client: AsyncOpenViking, sample_files: list[Path]
    ):
        """Test waiting for multiple resources to complete"""
        for f in sample_files:
            await client.add_resource(path=str(f), reason="Batch test")

        status = await client.wait_processed()

        assert isinstance(status, dict)
