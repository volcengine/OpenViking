# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for logger utilities."""

from types import SimpleNamespace

from openviking_cli.utils.logger import (
    ConcurrentTimedRotatingFileHandler,
    _create_log_handler,
)


def test_create_log_handler_uses_concurrent_rotating_handler(tmp_path):
    config = SimpleNamespace(
        log=SimpleNamespace(
            rotation=True,
            rotation_days=7,
            rotation_interval="midnight",
        )
    )
    handler = _create_log_handler(str(tmp_path / "app.log"), config)
    try:
        # concurrent-log-handler is a required dependency, so the real concurrent
        # handler must be in use here — not the stdlib fallback that the import
        # guard aliases to the same symbol name. Assert the concrete module so this
        # regression test cannot silently pass on the degraded fallback path.
        assert ConcurrentTimedRotatingFileHandler.__module__.startswith("concurrent_log_handler")
        assert isinstance(handler, ConcurrentTimedRotatingFileHandler)
    finally:
        handler.close()
