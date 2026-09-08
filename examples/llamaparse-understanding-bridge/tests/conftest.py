# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Shared bridge test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from openviking_llamaparse_bridge.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        llama_api_key="llx-test",
        bridge_api_key="bridge-secret-with-at-least-32-chars",
        llama_base_url="https://llama.test",
        public_url="http://bridge.test",
        timeout_seconds=5,
        artifact_ttl_seconds=300,
        artifact_cache_dir=tmp_path / "artifacts",
        organization_id="org-1",
        project_id="project-1",
    )
