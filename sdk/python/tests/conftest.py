# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

SDK_ROOT = Path(__file__).resolve().parents[1]

if str(SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(SDK_ROOT))


@pytest.fixture(autouse=True)
def _isolate_sdk_tests_from_default_ovcli_config(monkeypatch):
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", str(SDK_ROOT / ".missing-ovcli.conf"))


@pytest.fixture
def sdk_transport(monkeypatch):
    """Install an in-process HTTP transport while preserving SDK initialization."""
    http_client = httpx.AsyncClient

    def install(handler):
        def create(**kwargs):
            return http_client(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", create)

    return install
