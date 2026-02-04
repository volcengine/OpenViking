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
