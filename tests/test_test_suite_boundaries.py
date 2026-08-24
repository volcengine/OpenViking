# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Regression tests for test-suite ownership and optional provider imports."""

import builtins
import runpy
from pathlib import Path

import pytest

from tests import conftest as root_conftest


TESTS_ROOT = Path(__file__).parent
GEMINI_E2E_PATH = TESTS_ROOT / "integration" / "test_gemini_e2e.py"
GEMINI_EMBEDDER_MODULE = "openviking.models.embedder.gemini_embedders"


def test_root_collection_excludes_only_standalone_test_projects():
    """A root run must not absorb harnesses that own separate environments."""
    assert root_conftest.collect_ignore == ["api_test", "oc2ov_test"]


def _block_gemini_embedder_import(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    attempted_imports = []
    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == GEMINI_EMBEDDER_MODULE:
            attempted_imports.append(name)
            raise ModuleNotFoundError(
                f"No module named {GEMINI_EMBEDDER_MODULE!r}",
                name=GEMINI_EMBEDDER_MODULE,
            )
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    return attempted_imports


def test_gemini_e2e_module_loads_without_optional_provider(monkeypatch):
    """Pytest collection must not import the optional Gemini implementation."""
    attempted_imports = _block_gemini_embedder_import(monkeypatch)

    runpy.run_path(str(GEMINI_E2E_PATH), run_name="gemini_e2e_collection_contract")

    assert attempted_imports == []


def test_gemini_e2e_fixture_fails_loudly_when_provider_is_used(monkeypatch):
    """An activated Gemini test must fail, not skip, when its extra is absent."""
    _block_gemini_embedder_import(monkeypatch)
    namespace = runpy.run_path(
        str(GEMINI_E2E_PATH), run_name="gemini_e2e_runtime_contract"
    )
    fixture_function = namespace["embedder"].__wrapped__

    with pytest.raises(ModuleNotFoundError, match="gemini_embedders"):
        next(fixture_function())
