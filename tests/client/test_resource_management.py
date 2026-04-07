# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Resource management tests"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from openviking import AsyncOpenViking
from openviking.client import LocalClient
from openviking.server.identity import RequestContext, Role
from openviking.telemetry import get_current_telemetry
from openviking_cli.session.user_id import UserIdentifier


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

    async def test_local_client_add_resource_with_wait_preserves_queue_status(self):
        """Local SDK add_resource(wait=True) should keep queue_status and internal telemetry."""
        queue_status = {
            "Semantic": {"processed": 1, "error_count": 0, "errors": []},
            "Embedding": {"processed": 2, "error_count": 0, "errors": []},
        }
        seen: dict[str, object] = {}

        async def _fake_add_resource(**kwargs):
            telemetry = get_current_telemetry()
            seen["enabled"] = telemetry.enabled
            seen["telemetry_id"] = telemetry.telemetry_id
            seen["kwargs"] = kwargs
            return {
                "root_uri": "viking://resources/demo",
                "queue_status": queue_status,
            }

        client = LocalClient.__new__(LocalClient)
        client._ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
        client._service = SimpleNamespace(
            resources=SimpleNamespace(add_resource=_fake_add_resource)
        )

        result = await LocalClient.add_resource(
            client,
            path="/tmp/demo.md",
            reason="Test resource",
            wait=True,
            telemetry=False,
        )

        assert result["root_uri"] == "viking://resources/demo"
        assert result["queue_status"] == queue_status
        assert seen["enabled"] is True
        assert str(seen["telemetry_id"]).startswith("tm_")
        assert seen["kwargs"]["wait"] is True

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

    async def test_add_resource_with_to(self, client: AsyncOpenViking, sample_markdown_file: Path):
        """Test adding resource to specified target"""
        result = await client.add_resource(
            path=str(sample_markdown_file),
            to="viking://resources/custom/sample",
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


class TestWatchIntervalParameter:
    """Test watch_interval parameter propagation"""

    async def test_watch_interval_default_value(
        self, client: AsyncOpenViking, sample_markdown_file: Path
    ):
        """Test that watch_interval defaults to 0"""
        with patch.object(
            client._client, "add_resource", new_callable=AsyncMock
        ) as mock_add_resource:
            mock_add_resource.return_value = {"root_uri": "viking://test"}

            await client.add_resource(path=str(sample_markdown_file), reason="Test")

            call_kwargs = mock_add_resource.call_args[1]
            assert call_kwargs.get("watch_interval") == 0

    async def test_watch_interval_custom_value(
        self, client: AsyncOpenViking, sample_markdown_file: Path
    ):
        """Test that custom watch_interval value is propagated"""
        with patch.object(
            client._client, "add_resource", new_callable=AsyncMock
        ) as mock_add_resource:
            mock_add_resource.return_value = {"root_uri": "viking://test"}

            await client.add_resource(
                path=str(sample_markdown_file),
                reason="Test",
                watch_interval=5.0,
            )

            call_kwargs = mock_add_resource.call_args[1]
            assert call_kwargs.get("watch_interval") == 5.0

    async def test_watch_interval_propagates_to_local_client(
        self, sample_markdown_file: Path, test_data_dir: Path
    ):
        """Test that watch_interval propagates from AsyncOpenViking to LocalClient"""
        from openviking.client import LocalClient

        with patch.object(LocalClient, "add_resource", new_callable=AsyncMock) as mock_add_resource:
            mock_add_resource.return_value = {"root_uri": "viking://test"}

            from openviking import AsyncOpenViking

            await AsyncOpenViking.reset()
            client = AsyncOpenViking(path=str(test_data_dir))
            await client.initialize()

            try:
                await client.add_resource(
                    path=str(sample_markdown_file),
                    reason="Test",
                    watch_interval=10.0,
                )

                call_kwargs = mock_add_resource.call_args[1]
                assert call_kwargs.get("watch_interval") == 10.0
            finally:
                await client.close()
                await AsyncOpenViking.reset()

    async def test_watch_interval_zero_means_disabled(
        self, client: AsyncOpenViking, sample_markdown_file: Path
    ):
        """Test that watch_interval=0 means monitoring is disabled"""
        result = await client.add_resource(
            path=str(sample_markdown_file),
            reason="Test",
            watch_interval=0,
        )

        assert "root_uri" in result

    async def test_watch_interval_positive_value(
        self, client: AsyncOpenViking, sample_markdown_file: Path
    ):
        """Test that positive watch_interval value is accepted"""
        result = await client.add_resource(
            path=str(sample_markdown_file),
            reason="Test",
            watch_interval=2.5,
        )

        assert "root_uri" in result
