# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import MagicMock, patch

import pytest


class TestDiskStatusEndpoint:

    @pytest.mark.asyncio
    async def test_disk_status_not_configured(self, client):
        with patch(
            "openviking.server.routers.system.DiskPressureMonitor.get_instance",
            return_value=None,
        ):
            response = await client.get("/api/v1/system/disk")
            assert response.status_code == 200
            assert response.json()["status"] == "not_configured"

    @pytest.mark.asyncio
    async def test_disk_status_normal(self, client):
        mock_monitor = MagicMock()
        mock_monitor.get_status.return_value = {
            "state": "normal",
            "usage_percent": 45.0,
            "free_bytes": 107374182400,
            "free_gb": 100.0,
        }

        with patch(
            "openviking.server.routers.system.DiskPressureMonitor.get_instance",
            return_value=mock_monitor,
        ):
            response = await client.get("/api/v1/system/disk")
            assert response.status_code == 200
            assert response.json()["state"] == "normal"

    @pytest.mark.asyncio
    async def test_disk_status_warning(self, client):
        mock_monitor = MagicMock()
        mock_monitor.get_status.return_value = {
            "state": "warning",
            "usage_percent": 88.5,
            "free_bytes": 21474836480,
            "free_gb": 20.0,
        }

        with patch(
            "openviking.server.routers.system.DiskPressureMonitor.get_instance",
            return_value=mock_monitor,
        ):
            response = await client.get("/api/v1/system/disk")
            assert response.status_code == 200
            assert response.json()["state"] == "warning"

    @pytest.mark.asyncio
    async def test_disk_status_critical_returns_503(self, client):
        mock_monitor = MagicMock()
        mock_monitor.get_status.return_value = {
            "state": "critical",
            "usage_percent": 96.5,
            "free_bytes": 536870912,
            "free_gb": 0.5,
        }

        with patch(
            "openviking.server.routers.system.DiskPressureMonitor.get_instance",
            return_value=mock_monitor,
        ):
            response = await client.get("/api/v1/system/disk")
            assert response.status_code == 503
            assert response.json()["state"] == "critical"


class TestReadyEndpointDiskCheck:

    @pytest.mark.asyncio
    async def test_ready_includes_disk_check_ok(self, client):
        mock_monitor = MagicMock()
        mock_monitor.get_status.return_value = {
            "state": "normal",
            "usage_percent": 45.0,
        }

        with patch(
            "openviking.server.routers.system.DiskPressureMonitor.get_instance",
            return_value=mock_monitor,
        ):
            response = await client.get("/ready")
            data = response.json()
            assert "checks" in data
            assert data["checks"].get("disk") == "ok"

    @pytest.mark.asyncio
    async def test_ready_includes_disk_check_warning(self, client):
        mock_monitor = MagicMock()
        mock_monitor.get_status.return_value = {
            "state": "warning",
            "usage_percent": 88.5,
        }

        with patch(
            "openviking.server.routers.system.DiskPressureMonitor.get_instance",
            return_value=mock_monitor,
        ):
            response = await client.get("/ready")
            data = response.json()
            assert "checks" in data
            assert "warning: 88.5% used" in data["checks"].get("disk", "")

    @pytest.mark.asyncio
    async def test_ready_includes_disk_check_critical(self, client):
        mock_monitor = MagicMock()
        mock_monitor.get_status.return_value = {
            "state": "critical",
            "usage_percent": 96.5,
        }

        with patch(
            "openviking.server.routers.system.DiskPressureMonitor.get_instance",
            return_value=mock_monitor,
        ):
            response = await client.get("/ready")
            data = response.json()
            assert "checks" in data
            assert "critical: 96.5% used" in data["checks"].get("disk", "")

    @pytest.mark.asyncio
    async def test_ready_includes_disk_not_configured(self, client):
        with patch(
            "openviking.server.routers.system.DiskPressureMonitor.get_instance",
            return_value=None,
        ):
            response = await client.get("/ready")
            data = response.json()
            assert "checks" in data
            assert data["checks"].get("disk") == "not_configured"
