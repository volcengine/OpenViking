from __future__ import annotations

import sys
from pathlib import Path

import pytest

SDK_ROOT = Path(__file__).resolve().parents[1]

if str(SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(SDK_ROOT))


@pytest.fixture(autouse=True)
def _isolate_sdk_tests_from_default_ovcli_config(monkeypatch):
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", str(SDK_ROOT / ".missing-ovcli.conf"))
