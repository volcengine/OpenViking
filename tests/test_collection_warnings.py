"""Regression contracts for root and standalone test collection."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _offline_environment() -> dict[str, str]:
    """Keep collection checks deterministic and free of provider credentials."""
    environment = os.environ.copy()
    for name in (
        "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
        "OPENAI_ACCESS_TOKEN",
        "ANTHROPIC_API_KEY",
        "OPENVIKING_CONFIG_FILE",
    ):
        environment.pop(name, None)
    return environment


def test_root_collection_accepts_strict_markers_without_bot_import_path() -> None:
    """Root collection is strict and does not inherit the standalone bot path."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests",
            "--collect-only",
            "-q",
            "-o",
            "addopts=",
            "-p",
            "no:cacheprovider",
            "--no-cov",
            "--strict-markers",
            "-W",
            "error::pytest.PytestCollectionWarning",
        ],
        cwd=PROJECT_ROOT,
        env=_offline_environment(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "Unknown pytest.mark" not in combined
    assert "PytestCollectionWarning" not in combined

    root_config = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'pythonpath = ["."]' in root_config
    assert '"bot: tests executed by the standalone vikingbot harness"' in root_config

    bot_config = (PROJECT_ROOT / "bot/pytest.ini").read_text(encoding="utf-8")
    assert "pythonpath =" in bot_config
    assert "    ." in bot_config and "    .." in bot_config
    assert "bot: standalone vikingbot harness tests" in bot_config


def test_helper_support_classes_are_not_collected_as_tests() -> None:
    """Pydantic/data-access helpers must not trigger collection warnings."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/session/memory/test_json_stability.py",
            "tests/unit/test_accessors_registry.py",
            "--collect-only",
            "-q",
            "-o",
            "addopts=",
            "-p",
            "no:cacheprovider",
            "--no-cov",
            "-W",
            "error::pytest.PytestCollectionWarning",
        ],
        cwd=PROJECT_ROOT,
        env=_offline_environment(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PytestCollectionWarning" not in result.stdout + result.stderr


def test_openclaw_harness_has_its_own_collection_root() -> None:
    """The standalone harness must not collect its project file as a test module."""
    config = (PROJECT_ROOT / "tests/oc2ov_test/pyproject.toml").read_text(encoding="utf-8")
    assert 'testpaths = ["tests"]' in config
    assert 'pythonpath = [".", "utils"]' in config
