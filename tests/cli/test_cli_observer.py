# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""CLI observer endpoint tests (queue, models, system status)."""

import pytest

from conftest import ov

pytestmark = pytest.mark.cli_remote


class TestObserverQueue:
    def test_queue_status(self):
        r = ov(["observer", "queue", "-o", "json"])
        assert r["exit_code"] == 0, (
            f"ov observer queue should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        data = r["json"]
        assert data is not None, f"observer queue should return JSON, got stdout: {r['stdout'][:200]}"
        assert data.get("ok") is True, f"Expected ok=true, got {data.get('ok')}"


class TestObserverModels:
    def test_models_status(self):
        r = ov(["observer", "models", "-o", "json"])
        assert r["exit_code"] == 0, (
            f"ov observer models should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        data = r["json"]
        assert data is not None, f"observer models should return JSON, got stdout: {r['stdout'][:200]}"
        assert data.get("ok") is True, f"Expected ok=true, got {data.get('ok')}"


class TestObserverSystem:
    def test_system_status(self):
        r = ov(["observer", "system", "-o", "json"])
        assert r["exit_code"] == 0, (
            f"ov observer system should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        data = r["json"]
        assert data is not None, f"observer system should return JSON, got stdout: {r['stdout'][:200]}"
        assert data.get("ok") is True, f"Expected ok=true, got {data.get('ok')}"
