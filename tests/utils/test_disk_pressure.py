# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import threading
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the singleton before and after each test."""
    from openviking.utils.disk_pressure import DiskPressureMonitor

    DiskPressureMonitor.reset_instance()
    yield
    DiskPressureMonitor.reset_instance()


def make_disk_usage(total: int, used: int, free: int):
    mock = MagicMock()
    mock.total = total
    mock.used = used
    mock.free = free
    return mock


def test_initial_state_is_normal():
    from openviking.utils.disk_pressure import DiskPressureMonitor, DiskPressureState

    monitor = DiskPressureMonitor("/tmp")
    assert monitor.state == DiskPressureState.NORMAL


def test_state_transition_to_warning():
    from openviking.utils.disk_pressure import DiskPressureMonitor, DiskPressureState

    monitor = DiskPressureMonitor(
        "/tmp",
        warning_threshold_percent=80.0,
        critical_threshold_percent=95.0,
    )

    # 85% used -> WARNING
    with patch("shutil.disk_usage", return_value=make_disk_usage(100, 85, 15)):
        state = monitor.check()

    assert state == DiskPressureState.WARNING
    assert monitor.state == DiskPressureState.WARNING


def test_state_transition_to_critical():
    from openviking.utils.disk_pressure import DiskPressureMonitor, DiskPressureState

    monitor = DiskPressureMonitor(
        "/tmp",
        warning_threshold_percent=80.0,
        critical_threshold_percent=95.0,
    )

    # 96% used -> CRITICAL
    with patch("shutil.disk_usage", return_value=make_disk_usage(100, 96, 4)):
        state = monitor.check()

    assert state == DiskPressureState.CRITICAL
    assert monitor.state == DiskPressureState.CRITICAL


def test_state_transition_to_critical_by_min_free():
    from openviking.utils.disk_pressure import DiskPressureMonitor, DiskPressureState

    monitor = DiskPressureMonitor(
        "/tmp",
        warning_threshold_percent=80.0,
        critical_threshold_percent=95.0,
        min_free_bytes=1000,
    )

    # 50% used but only 500 bytes free (< 1000 min) -> CRITICAL
    with patch("shutil.disk_usage", return_value=make_disk_usage(1000, 500, 500)):
        state = monitor.check()

    assert state == DiskPressureState.CRITICAL


def test_check_write_allowed_normal():
    from openviking.utils.disk_pressure import DiskPressureMonitor

    monitor = DiskPressureMonitor("/tmp")

    with patch("shutil.disk_usage", return_value=make_disk_usage(100, 50, 50)):
        monitor.check()

    monitor.check_write_allowed()  # Should not raise


def test_check_write_allowed_warning():
    from openviking.utils.disk_pressure import DiskPressureMonitor

    monitor = DiskPressureMonitor(
        "/tmp",
        warning_threshold_percent=80.0,
        critical_threshold_percent=95.0,
    )

    # 85% used -> WARNING
    with patch("shutil.disk_usage", return_value=make_disk_usage(100, 85, 15)):
        monitor.check()

    monitor.check_write_allowed()  # Should not raise in WARNING state


def test_check_write_allowed_critical():
    from openviking.utils.disk_pressure import DiskPressureError, DiskPressureMonitor

    monitor = DiskPressureMonitor(
        "/tmp",
        warning_threshold_percent=80.0,
        critical_threshold_percent=95.0,
    )

    # 96% used -> CRITICAL
    with patch("shutil.disk_usage", return_value=make_disk_usage(100, 96, 4)):
        monitor.check()

    with pytest.raises(DiskPressureError):
        monitor.check_write_allowed()


def test_state_recovery():
    from openviking.utils.disk_pressure import DiskPressureMonitor, DiskPressureState

    monitor = DiskPressureMonitor(
        "/tmp",
        warning_threshold_percent=80.0,
        critical_threshold_percent=95.0,
    )

    # Go to CRITICAL
    with patch("shutil.disk_usage", return_value=make_disk_usage(100, 96, 4)):
        monitor.check()
    assert monitor.state == DiskPressureState.CRITICAL

    # Recover to NORMAL
    with patch("shutil.disk_usage", return_value=make_disk_usage(100, 50, 50)):
        monitor.check()
    assert monitor.state == DiskPressureState.NORMAL


def test_get_status_format():
    from openviking.utils.disk_pressure import DiskPressureMonitor

    monitor = DiskPressureMonitor(
        "/tmp",
        warning_threshold_percent=85.0,
        critical_threshold_percent=95.0,
        min_free_bytes=1073741824,
    )

    with patch("shutil.disk_usage", return_value=make_disk_usage(100_000_000_000, 45_000_000_000, 55_000_000_000)):
        monitor.check()

    status = monitor.get_status()

    assert status["state"] == "normal"
    assert "usage_percent" in status
    assert "free_bytes" in status
    assert "free_gb" in status
    assert status["warning_threshold_percent"] == 85.0
    assert status["critical_threshold_percent"] == 95.0
    assert status["min_free_bytes"] == 1073741824


def test_singleton_pattern():
    from openviking.utils.disk_pressure import DiskPressureMonitor

    monitor1 = DiskPressureMonitor.initialize("/tmp")
    monitor2 = DiskPressureMonitor.initialize("/other/path")
    monitor3 = DiskPressureMonitor.get_instance()

    assert monitor1 is monitor2
    assert monitor1 is monitor3


def test_get_instance_returns_none_before_init():
    from openviking.utils.disk_pressure import DiskPressureMonitor

    assert DiskPressureMonitor.get_instance() is None


def test_oserror_triggers_critical():
    from openviking.utils.disk_pressure import DiskPressureMonitor, DiskPressureState

    monitor = DiskPressureMonitor("/tmp")

    with patch("shutil.disk_usage", side_effect=OSError("disk error")):
        state = monitor.check()

    assert state == DiskPressureState.CRITICAL


def test_thread_safety():
    from openviking.utils.disk_pressure import DiskPressureMonitor

    monitor = DiskPressureMonitor("/tmp")
    errors = []

    def check_repeatedly():
        try:
            for _ in range(50):
                with patch("shutil.disk_usage", return_value=make_disk_usage(100, 50, 50)):
                    monitor.check()
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=check_repeatedly) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors


@pytest.mark.asyncio
async def test_monitor_start_stop_lifecycle():
    from openviking.utils.disk_pressure import DiskPressureMonitor

    monitor = DiskPressureMonitor("/tmp", check_interval_seconds=0.1)

    assert monitor._monitor_task is None

    await monitor.start()
    assert monitor._monitor_task is not None
    assert not monitor._monitor_task.done()

    await monitor.stop()
    assert monitor._monitor_task is None


@pytest.mark.asyncio
async def test_monitor_start_is_idempotent():
    from openviking.utils.disk_pressure import DiskPressureMonitor

    monitor = DiskPressureMonitor("/tmp", check_interval_seconds=0.1)

    await monitor.start()
    task1 = monitor._monitor_task

    await monitor.start()
    task2 = monitor._monitor_task

    assert task1 is task2

    await monitor.stop()


@pytest.mark.asyncio
async def test_monitor_stop_is_idempotent():
    from openviking.utils.disk_pressure import DiskPressureMonitor

    monitor = DiskPressureMonitor("/tmp", check_interval_seconds=0.1)

    await monitor.stop()
    await monitor.stop()


@pytest.mark.asyncio
async def test_monitor_performs_check_on_interval():
    import asyncio

    from openviking.utils.disk_pressure import DiskPressureMonitor

    monitor = DiskPressureMonitor("/tmp", check_interval_seconds=0.05)

    with patch("shutil.disk_usage", return_value=make_disk_usage(100, 50, 50)) as mock_usage:
        await monitor.start()
        await asyncio.sleep(0.15)
        await monitor.stop()

    assert mock_usage.call_count >= 2
