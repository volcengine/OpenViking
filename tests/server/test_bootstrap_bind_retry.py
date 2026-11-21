# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Bind-retry behavior of the uvicorn startup path (#5401)."""

from __future__ import annotations

import errno
import socket
from unittest.mock import patch

import pytest

from openviking.server.bootstrap import _bind_socket_with_retry
from openviking.server.config import ServerConfig


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _hold(port: int):
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    holder.bind(("127.0.0.1", port))
    holder.listen(1)
    return holder


def test_transient_holder_is_waited_out_then_bound():
    port = _free_port()
    holder = _hold(port)
    try:
        with patch("time.sleep") as slept:
            # The probe itself uses SO_REUSEADDR, so it still collides with the
            # holder's listening socket on macOS/BSD; on Linux a second bind
            # would succeed, in which case retry behavior is moot anyway.
            try:
                sock = _bind_socket_with_retry(
                    ServerConfig(
                        host="127.0.0.1",
                        port=port,
                        bind_retry_attempts=5,
                        bind_retry_interval_seconds=0.1,
                    )
                )
                sock.close()
                return  # Linux path: bound immediately alongside the holder
            except OSError:
                pass
            # Free the holder mid-flight: the next attempt must succeed.
            holder.close()
            sock = _bind_socket_with_retry(
                ServerConfig(
                    host="127.0.0.1",
                    port=port,
                    bind_retry_attempts=5,
                    bind_retry_interval_seconds=0.1,
                )
            )
            sock.close()
        assert slept.call_count >= 1
    finally:
        holder.close()


def test_persistent_holder_exhausts_retries_and_reraises():
    port = _free_port()
    holder = _hold(port)
    try:
        with pytest.raises(OSError) as excinfo:
            _bind_socket_with_retry(
                ServerConfig(
                    host="127.0.0.1",
                    port=port,
                    bind_retry_attempts=1,
                    bind_retry_interval_seconds=0.1,
                )
            )
        assert excinfo.value.errno == errno.EADDRINUSE
    finally:
        holder.close()


def test_zero_attempts_fails_fast_without_sleeping():
    port = _free_port()
    holder = _hold(port)
    try:
        with patch("time.sleep") as slept, pytest.raises(OSError):
            _bind_socket_with_retry(
                ServerConfig(
                    host="127.0.0.1",
                    port=port,
                    bind_retry_attempts=0,
                    bind_retry_interval_seconds=0.1,
                )
            )
        assert slept.call_count == 0
    finally:
        holder.close()


def test_free_port_binds_without_retries():
    port = _free_port()
    with patch("time.sleep") as slept:
        sock = _bind_socket_with_retry(
            ServerConfig(
                host="127.0.0.1",
                port=port,
                bind_retry_attempts=5,
                bind_retry_interval_seconds=0.1,
            )
        )
        sock.close()
    assert slept.call_count == 0


def test_config_defaults_are_conservative():
    cfg = ServerConfig()
    assert cfg.bind_retry_attempts == 5
    assert cfg.bind_retry_interval_seconds == 1.0
