# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from openviking_cli.utils import environment


def test_get_system_ram_gb_uses_sysconf_values():
    def fake_sysconf(name: str) -> int:
        values = {
            "SC_PHYS_PAGES": 4 * 1024,
            "SC_PAGE_SIZE": 1024 * 1024,
        }
        return values[name]

    with patch.object(environment.os, "sysconf", side_effect=fake_sysconf):
        assert environment.get_system_ram_gb() == 4


def test_get_system_ram_gb_uses_windows_fallback_when_sysconf_unavailable():
    class FakeKernel32:
        def GlobalMemoryStatusEx(self, stat_ptr):
            stat_ptr._obj.ullTotalPhys = 16 * 1024**3
            return 1

    with patch.object(environment.os, "sysconf", side_effect=AttributeError):
        with patch.object(
            environment.ctypes,
            "windll",
            SimpleNamespace(kernel32=FakeKernel32()),
            create=True,
        ):
            assert environment.get_system_ram_gb() == 16


def test_get_system_ram_gb_returns_zero_when_detection_fails():
    with patch.object(environment.os, "sysconf", side_effect=OSError):
        with patch.object(environment.ctypes, "windll", None, create=True):
            assert environment.get_system_ram_gb() == 0
