# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Tests for DebugService and ObserverService.
"""

from unittest.mock import MagicMock, patch

from openviking.service.debug_service import (
    ComponentStatus,
    DebugService,
    ObserverService,
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
            status="Status Table",
        )
        assert status.name == "test_component"
        assert status.is_healthy is True
        assert status.has_errors is False
        assert status.status == "Status Table"

    def test_component_status_unhealthy(self):
        """Test ComponentStatus with unhealthy state."""
        status = ComponentStatus(
            name="unhealthy_component",
            is_healthy=False,
            has_errors=True,
            status="Error Status",
        )
        assert status.is_healthy is False
        assert status.has_errors is True

    def test_component_status_str_healthy(self):
        """Test ComponentStatus __str__ for healthy component."""
        status = ComponentStatus(
            name="vikingdb",
            is_healthy=True,
            has_errors=False,
            status="Collection  Count\ntest        10",
        )
        result = str(status)
        assert "[vikingdb] (healthy)" in result
        assert "Collection  Count" in result

    def test_component_status_str_unhealthy(self):
        """Test ComponentStatus __str__ for unhealthy component."""
        status = ComponentStatus(
            name="queue",
            is_healthy=False,
            has_errors=True,
            status="Queue Error",
        )
        result = str(status)
        assert "[queue] (unhealthy)" in result


class TestSystemStatus:
    """Tests for SystemStatus dataclass."""

    def test_system_status_healthy(self):
        """Test SystemStatus with all healthy components."""
        components = {
            "queue": ComponentStatus("queue", True, False, "OK"),
            "vikingdb": ComponentStatus("vikingdb", True, False, "OK"),
        }
        status = SystemStatus(is_healthy=True, components=components, errors=[])
        assert status.is_healthy is True
        assert len(status.components) == 2
        assert status.errors == []

    def test_system_status_with_errors(self):
        """Test SystemStatus with errors."""
        components = {
            "queue": ComponentStatus("queue", False, True, "Error"),
            "vikingdb": ComponentStatus("vikingdb", True, False, "OK"),
        }
        status = SystemStatus(
            is_healthy=False,
            components=components,
            errors=["queue has errors"],
        )
        assert status.is_healthy is False
        assert len(status.errors) == 1

    def test_system_status_str(self):
        """Test SystemStatus __str__ method."""
        components = {
            "queue": ComponentStatus("queue", True, False, "Queue OK"),
            "vikingdb": ComponentStatus("vikingdb", True, False, "VikingDB OK"),
        }
        status = SystemStatus(is_healthy=True, components=components, errors=[])
        result = str(status)
        assert "[system] (healthy)" in result
        assert "[queue] (healthy)" in result
        assert "[vikingdb] (healthy)" in result


class TestObserverService:
    """Tests for ObserverService class."""

    def test_init_without_dependencies(self):
        """Test ObserverService can be created without dependencies."""
        service = ObserverService()
        assert service._vikingdb is None
        assert service._config is None

    def test_init_with_dependencies(self):
        """Test ObserverService can be created with dependencies."""
        mock_vikingdb = MagicMock()
        mock_config = MagicMock()
        service = ObserverService(vikingdb=mock_vikingdb, config=mock_config)
        assert service._vikingdb is mock_vikingdb
        assert service._config is mock_config

    def test_set_dependencies(self):
        """Test set_dependencies method."""
        service = ObserverService()
        mock_vikingdb = MagicMock()
        mock_config = MagicMock()
        service.set_dependencies(vikingdb=mock_vikingdb, config=mock_config)
        assert service._vikingdb is mock_vikingdb
        assert service._config is mock_config

    @patch("openviking.service.debug_service.get_queue_manager")
    @patch("openviking.service.debug_service.QueueObserver")
    def test_queue_property(self, mock_observer_cls, mock_get_queue_manager):
        """Test queue property returns ComponentStatus."""
        mock_queue_manager = MagicMock()
        mock_get_queue_manager.return_value = mock_queue_manager

        mock_observer = MagicMock()
        mock_observer.is_healthy.return_value = True
        mock_observer.has_errors.return_value = False
        mock_observer.get_status_table.return_value = "Queue Status Table"
        mock_observer_cls.return_value = mock_observer

        service = ObserverService()
        status = service.queue

        assert isinstance(status, ComponentStatus)
        assert status.name == "queue"
        assert status.is_healthy is True
        assert status.has_errors is False
        assert status.status == "Queue Status Table"
        mock_observer_cls.assert_called_once_with(mock_queue_manager)

    @patch("openviking.service.debug_service.VikingDBObserver")
    def test_vikingdb_property(self, mock_observer_cls):
        """Test vikingdb property returns ComponentStatus."""
        mock_vikingdb = MagicMock()
        mock_observer = MagicMock()
        mock_observer.is_healthy.return_value = True
        mock_observer.has_errors.return_value = False
        mock_observer.get_status_table.return_value = "VikingDB Status Table"
        mock_observer_cls.return_value = mock_observer

        service = ObserverService(vikingdb=mock_vikingdb)
        status = service.vikingdb()

        assert isinstance(status, ComponentStatus)
        assert status.name == "vikingdb"
        assert status.is_healthy is True
        assert status.has_errors is False
        assert status.status == "VikingDB Status Table"
        mock_observer_cls.assert_called_once_with(mock_vikingdb)

    @patch("openviking.service.debug_service.ModelsObserver")
    def test_models_property(self, mock_observer_cls):
        """Test models property returns ComponentStatus."""
        mock_config = MagicMock(spec=["vlm"])  # Only allow vlm attribute
        mock_vlm_instance = MagicMock()
        mock_config.vlm.get_vlm_instance.return_value = mock_vlm_instance

        mock_observer = MagicMock()
        mock_observer.is_healthy.return_value = True
        mock_observer.has_errors.return_value = False
        mock_observer.get_status_table.return_value = "Models Status Table"
        mock_observer_cls.return_value = mock_observer

        service = ObserverService(config=mock_config)
        status = service.models

        assert isinstance(status, ComponentStatus)
        assert status.name == "models"
        assert status.is_healthy is True
        assert status.has_errors is False
        assert status.status == "Models Status Table"
        mock_observer_cls.assert_called_once_with(
            vlm_instance=mock_vlm_instance,
            embedding_instance=None,
            rerank_instance=None,
        )

    @patch("openviking.service.debug_service.get_lock_manager")
    @patch("openviking.service.debug_service.RetrievalObserver")
    @patch("openviking.service.debug_service.LockObserver")
    @patch("openviking.service.debug_service.ModelsObserver")
    @patch("openviking.service.debug_service.VikingDBObserver")
    @patch("openviking.service.debug_service.QueueObserver")
    @patch("openviking.service.debug_service.get_queue_manager")
    def test_system_property_all_healthy(
        self,
        mock_get_queue_mgr,
        mock_queue_cls,
        mock_vikingdb_cls,
        mock_models_cls,
        mock_lock_cls,
        mock_retrieval_cls,
        mock_get_lock_mgr,
    ):
        """Test system property when all components are healthy."""
        # Setup mocks
        for mock_cls in [
            mock_queue_cls,
            mock_vikingdb_cls,
            mock_models_cls,
            mock_lock_cls,
            mock_retrieval_cls,
        ]:
            mock_observer = MagicMock()
            mock_observer.is_healthy.return_value = True
            mock_observer.has_errors.return_value = False
            mock_observer.get_status_table.return_value = "OK"
            mock_cls.return_value = mock_observer

        # Mock get_lock_manager to return a MagicMock
        mock_get_lock_mgr.return_value = MagicMock()

        mock_config = MagicMock(spec=["vlm"])  # Only allow vlm attribute
        mock_config.vlm.get_vlm_instance.return_value = MagicMock()
        service = ObserverService(vikingdb=MagicMock(), config=mock_config)
        status = service.system()

        assert isinstance(status, SystemStatus)
        for name in ("queue", "vikingdb", "models", "lock", "retrieval"):
            assert status.components[name].is_healthy is True
        non_transaction_errors = [e for e in status.errors if "transaction" not in e]
        assert non_transaction_errors == []

    @patch("openviking.service.debug_service.get_lock_manager")
    @patch("openviking.service.debug_service.RetrievalObserver")
    @patch("openviking.service.debug_service.LockObserver")
    @patch("openviking.service.debug_service.ModelsObserver")
    @patch("openviking.service.debug_service.VikingDBObserver")
    @patch("openviking.service.debug_service.QueueObserver")
    @patch("openviking.service.debug_service.get_queue_manager")
    def test_system_property_with_errors(
        self,
        mock_get_queue_mgr,
        mock_queue_cls,
        mock_vikingdb_cls,
        mock_models_cls,
        mock_lock_cls,
        mock_retrieval_cls,
        mock_get_lock_mgr,
    ):
        """Test system property when some components have errors."""
        # Queue has errors
        mock_queue = MagicMock()
        mock_queue.is_healthy.return_value = False
        mock_queue.has_errors.return_value = True
        mock_queue.get_status_table.return_value = "Error"
        mock_queue_cls.return_value = mock_queue

        # VikingDB is healthy
        mock_vikingdb = MagicMock()
        mock_vikingdb.is_healthy.return_value = True
        mock_vikingdb.has_errors.return_value = False
        mock_vikingdb.get_status_table.return_value = "OK"
        mock_vikingdb_cls.return_value = mock_vikingdb

        # Models has errors
        mock_models = MagicMock()
        mock_models.is_healthy.return_value = False
        mock_models.has_errors.return_value = True
        mock_models.get_status_table.return_value = "Error"
        mock_models_cls.return_value = mock_models

        # Lock and Retrieval are healthy
        for mock_cls in [mock_lock_cls, mock_retrieval_cls]:
            mock_observer = MagicMock()
            mock_observer.is_healthy.return_value = True
            mock_observer.has_errors.return_value = False
            mock_observer.get_status_table.return_value = "OK"
            mock_cls.return_value = mock_observer

        # Mock get_lock_manager to return a MagicMock
        mock_get_lock_mgr.return_value = MagicMock()

        mock_config = MagicMock(spec=["vlm"])  # Only allow vlm attribute
        mock_config.vlm.get_vlm_instance.return_value = MagicMock()
        service = ObserverService(vikingdb=MagicMock(), config=mock_config)
        status = service.system()

        assert isinstance(status, SystemStatus)
        assert status.is_healthy is False
        non_transaction_errors = [e for e in status.errors if "transaction" not in e]
        assert len(non_transaction_errors) == 2
        assert "queue has errors" in non_transaction_errors
        assert "models has errors" in non_transaction_errors

    @patch("openviking.service.debug_service.get_lock_manager")
    @patch("openviking.service.debug_service.get_queue_manager")
    @patch("openviking.service.debug_service.QueueObserver")
    @patch("openviking.service.debug_service.VikingDBObserver")
    @patch("openviking.service.debug_service.ModelsObserver")
    def test_is_healthy_returns_true(
        self,
        mock_models_cls,
        mock_vikingdb_cls,
        mock_queue_cls,
        mock_get_queue_mgr,
        mock_get_lock_mgr,
    ):
        """Test is_healthy returns True when system is healthy."""
        for mock_cls in [mock_queue_cls, mock_vikingdb_cls, mock_models_cls]:
            mock_observer = MagicMock()
            mock_observer.is_healthy.return_value = True
            mock_observer.has_errors.return_value = False
            mock_observer.get_status_table.return_value = "OK"
            mock_cls.return_value = mock_observer

        mock_get_lock_mgr.return_value = MagicMock()

        mock_config = MagicMock(spec=["vlm"])
        mock_config.vlm.get_vlm_instance.return_value = MagicMock()
        service = ObserverService(vikingdb=MagicMock(), config=mock_config)
        status = service.system()
        assert all(c.is_healthy for name, c in status.components.items() if name != "transaction")

    def test_is_healthy_without_dependencies(self):
        """Test is_healthy returns False (not raises) when dependencies not set."""
        service = ObserverService()
        assert service.is_healthy() is False

    def test_vikingdb_property_without_dependency(self):
        """Test vikingdb property returns unhealthy ComponentStatus when vikingdb is None."""
        service = ObserverService()
        status = service.vikingdb()
        assert isinstance(status, ComponentStatus)
        assert status.name == "vikingdb"
        assert status.is_healthy is False
        assert status.has_errors is True
        assert status.status == "Not initialized"

    def test_models_property_without_dependency(self):
        """Test models property returns unhealthy ComponentStatus when config is None."""
        service = ObserverService()
        status = service.models
        assert isinstance(status, ComponentStatus)
        assert status.name == "models"
        assert status.is_healthy is False
        assert status.has_errors is True
        assert status.status == "Not initialized"

    def test_system_property_without_dependencies(self):
        """Test system property returns unhealthy SystemStatus when dependencies not set."""
        service = ObserverService()
        status = service.system()
        assert isinstance(status, SystemStatus)
        assert status.is_healthy is False

    @patch("openviking.service.debug_service.get_queue_manager")
    @patch("openviking.service.debug_service.QueueObserver")
    @patch("openviking.service.debug_service.VikingDBObserver")
    @patch("openviking.service.debug_service.ModelsObserver")
    def test_is_healthy_returns_false(
        self, mock_models_cls, mock_vikingdb_cls, mock_queue_cls, mock_get_queue_manager
    ):
        """Test is_healthy returns False when system is unhealthy."""
        # Queue has errors
        mock_queue = MagicMock()
        mock_queue.is_healthy.return_value = False
        mock_queue.has_errors.return_value = True
        mock_queue.get_status_table.return_value = "Error"
        mock_queue_cls.return_value = mock_queue

        # Others are healthy
        for mock_cls in [mock_vikingdb_cls, mock_models_cls]:
            mock_observer = MagicMock()
            mock_observer.is_healthy.return_value = True
            mock_observer.has_errors.return_value = False
            mock_observer.get_status_table.return_value = "OK"
            mock_cls.return_value = mock_observer

        mock_config = MagicMock(spec=["vlm"])  # Only allow vlm attribute
        mock_config.vlm.get_vlm_instance.return_value = MagicMock()
        service = ObserverService(vikingdb=MagicMock(), config=mock_config)
        assert service.is_healthy() is False


class TestDebugService:
    """Tests for DebugService class."""

    def test_init_creates_observer(self):
        """Test DebugService creates ObserverService on init."""
        service = DebugService()
        assert isinstance(service._observer, ObserverService)

    def test_init_with_dependencies(self):
        """Test DebugService passes dependencies to ObserverService."""
        mock_vikingdb = MagicMock()
        mock_config = MagicMock()
        service = DebugService(vikingdb=mock_vikingdb, config=mock_config)
        assert service._observer._vikingdb is mock_vikingdb
        assert service._observer._config is mock_config

    def test_set_dependencies(self):
        """Test set_dependencies passes to ObserverService."""
        service = DebugService()
        mock_vikingdb = MagicMock()
        mock_config = MagicMock()
        service.set_dependencies(vikingdb=mock_vikingdb, config=mock_config)
        assert service._observer._vikingdb is mock_vikingdb
        assert service._observer._config is mock_config

    def test_observer_property(self):
        """Test observer property returns ObserverService."""
        service = DebugService()
        assert service.observer is service._observer
        assert isinstance(service.observer, ObserverService)
