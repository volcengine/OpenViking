"""An intentionally absent WM overview is a successful diagnostic result."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def healthcheck():
    path = (
        Path(__file__).parents[2] / "examples/openclaw-plugin/health_check_tools/ov-healthcheck.py"
    )
    spec = importlib.util.spec_from_file_location("ov_healthcheck_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


@pytest.mark.parametrize("enabled", [False, True])
def test_visibility_wait_only_requires_overview_when_requested(healthcheck, monkeypatch, enabled):
    calls = []
    sleeps = []

    def context(_session_id):
        calls.append(True)
        return {"latest_archive_overview": "summary" if len(calls) > 1 else ""}

    inspector = SimpleNamespace(
        get_session=lambda _: {"commit_count": 1, "stats": {"memories_extracted": {"profile": 1}}},
        get_context=context,
    )
    monkeypatch.setattr(healthcheck, "extract_memory_total", lambda _: 1)
    monkeypatch.setattr(healthcheck.time, "sleep", sleeps.append)
    healthcheck.wait_for_commit_visibility(
        inspector, "s", timeout_seconds=10, verbose=False, require_overview=enabled
    )
    assert len(calls) == (2 if enabled else 1)
    assert len(sleeps) == (1 if enabled else 0)
