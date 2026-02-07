#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for AGFSManager._check_port_available() socket leak fix."""

import gc
import os
import socket
import sys
import warnings

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from openviking.agfs_manager import AGFSManager


def _make_manager(port: int) -> AGFSManager:
    """Create a minimal AGFSManager with only the port attribute set."""
    mgr = AGFSManager.__new__(AGFSManager)
    mgr.port = port
    return mgr


class TestCheckPortAvailable:
    """Test _check_port_available() properly closes sockets."""

    def test_available_port_no_leak(self):
        """Socket should be closed after successful port check."""
        mgr = _make_manager(0)  # port 0 = OS picks a free port
        # Should not raise and should not leak
        mgr._check_port_available()

    def test_occupied_port_raises_runtime_error(self):
        """Should raise RuntimeError when port is in use."""
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.bind(("localhost", 0))
        port = blocker.getsockname()[1]
        blocker.listen(1)

        mgr = _make_manager(port)
        try:
            with pytest.raises(RuntimeError, match="already in use"):
                mgr._check_port_available()
        finally:
            blocker.close()

    def test_occupied_port_no_resource_warning(self):
        """Socket must be closed even when port is occupied (no ResourceWarning)."""
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.bind(("localhost", 0))
        port = blocker.getsockname()[1]
        blocker.listen(1)

        mgr = _make_manager(port)
        try:
            with pytest.raises(RuntimeError):
                mgr._check_port_available()

            # Force GC and check for ResourceWarning about unclosed socket
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always", ResourceWarning)
                gc.collect()
                resource_warnings = [x for x in w if issubclass(x.category, ResourceWarning)]
                assert len(resource_warnings) == 0, f"Socket leaked: {resource_warnings}"
        finally:
            blocker.close()
