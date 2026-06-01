# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared fixtures for migration tests."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect OPENVIKING_CONFIG_DIR to a temp directory for test isolation.

    This ensures that state files (e.g. embedding_migration_state.json) are
    created in a per-test temporary directory instead of the real ~/.openviking.
    """
    config_dir = tmp_path / ".openviking"
    config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPENVIKING_CONFIG_DIR", str(config_dir))


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    """Provide a temporary directory for migration test artifacts."""
    return tmp_path


@pytest.fixture
def sample_config() -> dict:
    """Provide a minimal migration configuration skeleton.

    Each value is a full EmbeddingConfig dict, which requires at least
    a ``dense`` sub-field with provider/model/dimension.
    """
    return {
        "source": {
            "dense": {
                "provider": "openai",
                "model": "text-embedding-3-large",
                "dimension": 3072,
            },
        },
        "target": {
            "dense": {
                "provider": "volcengine",
                "model": "doubao-embedding-vision-251215",
                "dimension": 1024,
            },
        },
    }
