# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Tests for DebugService.
"""

from unittest.mock import MagicMock, patch

import pytest

from openviking.service.debug_service import (
    ComponentStatus,
    DebugService,
    SystemStatus,
)


class TestComponentStatus:
    """Tests for ComponentStatus dataclass."""

    def test_component_status_creation(self):
        """Test ComponentStatus can be created with all fields."""
        status = ComponentStatus(
            name="test_component",
            is_healthy=True,
            has_errors=False,
            details={"key": "value"},
        )
        assert status.name == "test_component"
        assert status.is_healthy is True
        assert status.has_errors is False
        assert status.details == {"key": "value"}

    def test_component_status_unhealthy(self):
        """Test ComponentStatus with unhealthy state."""
        status = ComponentStatus(
            name="unhealthy_component",
            is_healthy=False,
            has_errors=True,
            details={"error": "connection failed"},
        )
        assert status.is_healthy is False
        assert status.has_errors is True


class TestSystemStatus:
    """Tests for SystemStatus dataclass."""

    def test_system_status_healthy(self):
        """Test SystemStatus with all healthy components."""
        components = {
            "queue": ComponentStatus("queue", True, False, {}),
            "vikingdb": ComponentStatus("vikingdb", True, False, {}),
        }
        status = SystemStatus(is_healthy=True, components=components, errors=[])
        assert status.is_healthy is True
        assert len(status.components) == 2
        assert status.errors == []

    def test_system_status_with_errors(self):
        """Test SystemStatus with errors."""
        components = {
            "queue": ComponentStatus("queue", False, True, {}),
            "vikingdb": ComponentStatus("vikingdb", True, False, {}),
        }
        status = SystemStatus(
            is_healthy=False,
            components=components,
            errors=["queue has errors"],
        )
        assert status.is_healthy is False
        assert len(status.errors) == 1


class TestDebugService:
    """Tests for DebugService class."""

    def test_init_without_dependencies(self):
        """Test DebugService can be created without dependencies."""
        service = DebugService()
        assert service._vikingdb is None
        assert service._config is None

    def test_init_with_dependencies(self):
        """Test DebugService can be created with dependencies."""
        mock_vikingdb = MagicMock()
        mock_config = MagicMock()
        service = DebugService(vikingdb=mock_vikingdb, config=mock_config)
        assert service._vikingdb is mock_vikingdb
        assert service._config is mock_config

    def test_set_dependencies(self):
        """Test set_dependencies method."""
        service = DebugService()
        mock_vikingdb = MagicMock()
        mock_config = MagicMock()
        service.set_dependencies(vikingdb=mock_vikingdb, config=mock_config)
        assert service._vikingdb is mock_vikingdb
        assert service._config is mock_config

    @patch("openviking.service.debug_service.get_queue_manager")
    @patch("openviking.service.debug_service.QueueObserver")
    def test_get_queue_status(self, mock_observer_cls, mock_get_queue_manager):
        """Test get_queue_status returns ComponentStatus."""
        mock_queue_manager = MagicMock()
        mock_get_queue_manager.return_value = mock_queue_manager

        mock_observer = MagicMock()
        mock_observer.is_healthy.return_value = True
        mock_observer.has_errors.return_value = False
        mock_observer.get_status_table.return_value = "Queue Status Table"
        mock_observer_cls.return_value = mock_observer

        service = DebugService()
        status = service.get_queue_status()

        assert isinstance(status, ComponentStatus)
        assert status.name == "queue"
        assert status.is_healthy is True
        assert status.has_errors is False
        assert status.details["status_table"] == "Queue Status Table"
        mock_observer_cls.assert_called_once_with(mock_queue_manager)

    @patch("openviking.service.debug_service.VikingDBObserver")
    def test_get_vikingdb_status(self, mock_observer_cls):
        """Test get_vikingdb_status returns ComponentStatus."""
        mock_vikingdb = MagicMock()
        mock_observer = MagicMock()
        mock_observer.is_healthy.return_value = True
        mock_observer.has_errors.return_value = False
        mock_observer.get_status_table.return_value = "VikingDB Status Table"
        mock_observer_cls.return_value = mock_observer

        service = DebugService(vikingdb=mock_vikingdb)
        status = service.get_vikingdb_status()

        assert isinstance(status, ComponentStatus)
        assert status.name == "vikingdb"
        assert status.is_healthy is True
        assert status.has_errors is False
        assert status.details["status_table"] == "VikingDB Status Table"
        mock_observer_cls.assert_called_once_with(mock_vikingdb)

    @patch("openviking.service.debug_service.VLMObserver")
    def test_get_vlm_status(self, mock_observer_cls):
        """Test get_vlm_status returns ComponentStatus."""
        mock_config = MagicMock()
        mock_vlm_instance = MagicMock()
        mock_config.vlm.get_vlm_instance.return_value = mock_vlm_instance

        mock_observer = MagicMock()
        mock_observer.is_healthy.return_value = True
        mock_observer.has_errors.return_value = False
        mock_observer.get_status_table.return_value = "VLM Status Table"
        mock_observer_cls.return_value = mock_observer

        service = DebugService(config=mock_config)
        status = service.get_vlm_status()

        assert isinstance(status, ComponentStatus)
        assert status.name == "vlm"
        assert status.is_healthy is True
        assert status.has_errors is False
        assert status.details["status_table"] == "VLM Status Table"
        mock_observer_cls.assert_called_once_with(mock_vlm_instance)

    @patch.object(DebugService, "get_queue_status")
    @patch.object(DebugService, "get_vikingdb_status")
    @patch.object(DebugService, "get_vlm_status")
    def test_get_system_status_all_healthy(
        self, mock_vlm, mock_vikingdb, mock_queue
    ):
        """Test get_system_status when all components are healthy."""
        mock_queue.return_value = ComponentStatus("queue", True, False, {})
        mock_vikingdb.return_value = ComponentStatus("vikingdb", True, False, {})
        mock_vlm.return_value = ComponentStatus("vlm", True, False, {})

        service = DebugService()
        status = service.get_system_status()

        assert isinstance(status, SystemStatus)
        assert status.is_healthy is True
        assert len(status.components) == 3
        assert status.errors == []

    @patch.object(DebugService, "get_queue_status")
    @patch.object(DebugService, "get_vikingdb_status")
    @patch.object(DebugService, "get_vlm_status")
    def test_get_system_status_with_errors(
        self, mock_vlm, mock_vikingdb, mock_queue
    ):
        """Test get_system_status when some components have errors."""
        mock_queue.return_value = ComponentStatus("queue", False, True, {})
        mock_vikingdb.return_value = ComponentStatus("vikingdb", True, False, {})
        mock_vlm.return_value = ComponentStatus("vlm", False, True, {})

        service = DebugService()
        status = service.get_system_status()

        assert isinstance(status, SystemStatus)
        assert status.is_healthy is False
        assert len(status.errors) == 2
        assert "queue has errors" in status.errors
        assert "vlm has errors" in status.errors

    @patch.object(DebugService, "get_system_status")
    def test_is_healthy_returns_true(self, mock_get_system_status):
        """Test is_healthy returns True when system is healthy."""
        mock_get_system_status.return_value = SystemStatus(
            is_healthy=True, components={}, errors=[]
        )
        service = DebugService()
        assert service.is_healthy() is True

    @patch.object(DebugService, "get_system_status")
    def test_is_healthy_returns_false(self, mock_get_system_status):
        """Test is_healthy returns False when system is unhealthy."""
        mock_get_system_status.return_value = SystemStatus(
            is_healthy=False, components={}, errors=["error"]
        )
        service = DebugService()
        assert service.is_healthy() is False
