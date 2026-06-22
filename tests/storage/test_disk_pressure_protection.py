# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.utils.disk_pressure import DiskPressureError, DiskPressureState


@pytest.fixture(autouse=True)
def reset_singleton():
    from openviking.utils.disk_pressure import DiskPressureMonitor

    DiskPressureMonitor.reset_instance()
    yield
    DiskPressureMonitor.reset_instance()


class TestVikingFSWriteProtection:
    @pytest.mark.asyncio
    async def test_write_proceeds_when_monitor_not_initialized(self):
        from openviking.utils.disk_pressure import DiskPressureMonitor

        assert DiskPressureMonitor.get_instance() is None

        mock_agfs = MagicMock()
        mock_async_agfs = AsyncMock()
        mock_async_agfs.write = AsyncMock(return_value="mock_result")

        with (
            patch("openviking.storage.viking_fs.AsyncAGFSClient", return_value=mock_async_agfs),
            patch("openviking.storage.viking_fs._instance") as mock_instance,
        ):
            from openviking.storage.viking_fs import VikingFS

            fs = VikingFS(agfs=mock_agfs)
            fs._async_agfs = mock_async_agfs
            fs._ensure_mutable_access = MagicMock()
            fs._uri_to_path = MagicMock(return_value="/test/path")

            result = await fs.write("viking://test/file.txt", b"data")
            assert result == "mock_result"
            mock_async_agfs.write.assert_called_once()

    @pytest.mark.asyncio
    async def test_write_proceeds_when_disk_normal(self):
        from openviking.utils.disk_pressure import DiskPressureMonitor

        monitor = DiskPressureMonitor.initialize("/tmp")
        monitor._state = DiskPressureState.NORMAL

        mock_agfs = MagicMock()
        mock_async_agfs = AsyncMock()
        mock_async_agfs.write = AsyncMock(return_value="mock_result")

        with patch("openviking.storage.viking_fs.AsyncAGFSClient", return_value=mock_async_agfs):
            from openviking.storage.viking_fs import VikingFS

            fs = VikingFS(agfs=mock_agfs)
            fs._async_agfs = mock_async_agfs
            fs._ensure_mutable_access = MagicMock()
            fs._uri_to_path = MagicMock(return_value="/test/path")

            result = await fs.write("viking://test/file.txt", b"data")
            assert result == "mock_result"

    @pytest.mark.asyncio
    async def test_write_blocked_when_critical(self):
        from openviking.pyagfs.exceptions import AGFSIOError
        from openviking.utils.disk_pressure import DiskPressureMonitor

        monitor = DiskPressureMonitor.initialize("/tmp")
        monitor._state = DiskPressureState.CRITICAL
        monitor._last_usage_percent = 96.0
        monitor._last_free_bytes = 500_000_000

        mock_agfs = MagicMock()
        mock_async_agfs = AsyncMock()

        with patch("openviking.storage.viking_fs.AsyncAGFSClient", return_value=mock_async_agfs):
            from openviking.storage.viking_fs import VikingFS

            fs = VikingFS(agfs=mock_agfs)
            fs._async_agfs = mock_async_agfs
            fs._ensure_mutable_access = MagicMock()
            fs._uri_to_path = MagicMock(return_value="/test/path")

            with pytest.raises(AGFSIOError) as exc_info:
                await fs.write("viking://test/file.txt", b"data")

            assert "CRITICAL" in str(exc_info.value)
            mock_async_agfs.write.assert_not_called()


class TestQueueEnqueueProtection:
    @pytest.mark.asyncio
    async def test_enqueue_proceeds_when_monitor_not_initialized(self):
        from openviking.utils.disk_pressure import DiskPressureMonitor

        assert DiskPressureMonitor.get_instance() is None

        mock_agfs = MagicMock()
        mock_async_agfs = AsyncMock()
        mock_async_agfs.mkdir = AsyncMock()
        mock_async_agfs.write = AsyncMock(return_value="msg_123")

        with patch(
            "openviking.storage.queuefs.named_queue.AsyncAGFSClient",
            return_value=mock_async_agfs,
        ):
            from openviking.storage.queuefs.named_queue import NamedQueue

            queue = NamedQueue(mock_agfs, "/mount", "test_queue")
            queue._async_agfs = mock_async_agfs

            result = await queue.enqueue({"test": "data"})
            assert result == "msg_123"
            mock_async_agfs.write.assert_called_once()

    @pytest.mark.asyncio
    async def test_enqueue_proceeds_when_disk_normal(self):
        from openviking.utils.disk_pressure import DiskPressureMonitor

        monitor = DiskPressureMonitor.initialize("/tmp")
        monitor._state = DiskPressureState.NORMAL

        mock_agfs = MagicMock()
        mock_async_agfs = AsyncMock()
        mock_async_agfs.mkdir = AsyncMock()
        mock_async_agfs.write = AsyncMock(return_value="msg_123")

        with patch(
            "openviking.storage.queuefs.named_queue.AsyncAGFSClient",
            return_value=mock_async_agfs,
        ):
            from openviking.storage.queuefs.named_queue import NamedQueue

            queue = NamedQueue(mock_agfs, "/mount", "test_queue")
            queue._async_agfs = mock_async_agfs

            result = await queue.enqueue({"test": "data"})
            assert result == "msg_123"

    @pytest.mark.asyncio
    async def test_enqueue_blocked_when_critical(self):
        from openviking.utils.disk_pressure import DiskPressureMonitor

        monitor = DiskPressureMonitor.initialize("/tmp")
        monitor._state = DiskPressureState.CRITICAL
        monitor._last_usage_percent = 96.0
        monitor._last_free_bytes = 500_000_000

        mock_agfs = MagicMock()
        mock_async_agfs = AsyncMock()

        with patch(
            "openviking.storage.queuefs.named_queue.AsyncAGFSClient",
            return_value=mock_async_agfs,
        ):
            from openviking.storage.queuefs.named_queue import NamedQueue

            queue = NamedQueue(mock_agfs, "/mount", "test_queue")
            queue._async_agfs = mock_async_agfs

            with pytest.raises(DiskPressureError) as exc_info:
                await queue.enqueue({"test": "data"})

            assert "CRITICAL" in str(exc_info.value)
            mock_async_agfs.write.assert_not_called()


class TestResourceProcessorProtection:
    @pytest.mark.asyncio
    async def test_process_resource_blocked_when_critical(self):
        from openviking.utils.disk_pressure import DiskPressureMonitor
        from openviking_cli.exceptions import OpenVikingError

        monitor = DiskPressureMonitor.initialize("/tmp")
        monitor._state = DiskPressureState.CRITICAL
        monitor._last_usage_percent = 96.0
        monitor._last_free_bytes = 500_000_000

        mock_vikingdb = MagicMock()
        mock_vikingdb.get_embedder = MagicMock(return_value=MagicMock())
        mock_ctx = MagicMock()

        from openviking.utils.resource_processor import ResourceProcessor

        processor = ResourceProcessor(vikingdb=mock_vikingdb)

        with pytest.raises(OpenVikingError) as exc_info:
            await processor.process_resource("/some/path", ctx=mock_ctx)

        assert "disk pressure" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_process_resource_proceeds_when_monitor_not_initialized(self):
        from openviking.utils.disk_pressure import DiskPressureMonitor

        assert DiskPressureMonitor.get_instance() is None

        mock_vikingdb = MagicMock()
        mock_vikingdb.get_embedder = MagicMock(return_value=MagicMock())
        mock_ctx = MagicMock()

        from openviking.utils.resource_processor import ResourceProcessor

        processor = ResourceProcessor(vikingdb=mock_vikingdb)

        with patch.object(processor, "_get_media_processor") as mock_media:
            mock_media_processor = MagicMock()
            mock_parse_result = MagicMock()
            mock_parse_result.temp_dir_path = None
            mock_parse_result.warnings = ["test warning"]
            mock_media_processor.process = AsyncMock(return_value=mock_parse_result)
            mock_media.return_value = mock_media_processor

            with patch("openviking.utils.resource_processor.get_viking_fs") as mock_fs:
                mock_fs_instance = MagicMock()
                mock_fs_instance.bind_request_context = MagicMock(return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock()))
                mock_fs.return_value = mock_fs_instance

                result = await processor.process_resource("/some/path", ctx=mock_ctx)
                assert result["status"] == "error"
