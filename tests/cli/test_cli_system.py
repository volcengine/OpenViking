# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""CLI system endpoint tests (health, status, wait)."""

import pytest

from conftest import ov

pytestmark = pytest.mark.cli_remote


class TestSystemHealth:
    def test_health(self):
        r = ov(["health", "-o", "json"])
        assert r["exit_code"] == 0, (
            f"ov health should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        data = r["json"]
        if data is not None:
            assert data.get("ok") is True or "healthy" in str(data).lower(), (
                f"health check should indicate healthy, got: {r['stdout'][:200]}"
            )


class TestSystemStatus:
    def test_status(self):
        r = ov(["status", "-o", "json"])
        assert r["exit_code"] == 0, (
            f"ov status should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        data = r["json"]
        assert data is not None, f"status should return JSON, got stdout: {r['stdout'][:200]}"
        assert data.get("ok") is True, f"Expected ok=true, got {data.get('ok')}"
        assert "result" in data, "'result' field should exist in status response"


class TestSystemWait:
    def test_wait_with_timeout(self):
        r = ov(["wait", "--timeout", "30", "-o", "json"], timeout=60)
        assert r["exit_code"] == 0, (
            f"ov wait should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
