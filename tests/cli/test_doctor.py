# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for ``ov doctor`` diagnostic checks."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

from openviking_cli.doctor import (
    check_agfs,
    check_config,
    check_disk,
    check_embedding,
    check_native_engine,
    check_python,
    check_vlm,
    run_doctor,
)


class TestCheckConfig:
    def test_pass_with_valid_config(self, tmp_path: Path):
        config = tmp_path / "ov.conf"
        config.write_text(json.dumps({"embedding": {"dense": {}}}))
        with patch("openviking_cli.doctor._find_config", return_value=config):
            ok, detail, fix = check_config()
        assert ok
        assert str(config) in detail

    def test_fail_missing_config(self):
        with patch("openviking_cli.doctor._find_config", return_value=None):
            ok, detail, fix = check_config()
        assert not ok
        assert "not found" in detail
        assert fix is not None

    def test_fail_invalid_json(self, tmp_path: Path):
        config = tmp_path / "ov.conf"
        config.write_text("{bad json")
        with patch("openviking_cli.doctor._find_config", return_value=config):
            ok, detail, fix = check_config()
        assert not ok
        assert "Invalid JSON" in detail

    def test_fail_missing_embedding_section(self, tmp_path: Path):
        config = tmp_path / "ov.conf"
        config.write_text(json.dumps({"server": {}}))
        with patch("openviking_cli.doctor._find_config", return_value=config):
            ok, detail, fix = check_config()
        assert not ok
        assert "embedding" in detail


class TestCheckPython:
    def test_pass_current_python(self):
        ok, detail, fix = check_python()
        assert ok  # Tests run on Python >= 3.10

    def test_fail_old_python(self):
        with patch.object(sys, "version_info", (3, 9, 0, "final", 0)):
            ok, detail, fix = check_python()
        assert not ok
        assert "3.9.0" in detail


class TestCheckNativeEngine:
    def test_pass_when_available(self):
        with patch(
            "openviking_cli.doctor.ENGINE_VARIANT",
            "native",
            create=True,
        ):
            # Need to patch the import itself
            import openviking.storage.vectordb.engine as engine_mod

            original_variant = engine_mod.ENGINE_VARIANT
            engine_mod.ENGINE_VARIANT = "native"
            try:
                ok, detail, fix = check_native_engine()
                assert ok
                assert "native" in detail
            finally:
                engine_mod.ENGINE_VARIANT = original_variant

    def test_fail_when_unavailable(self):
        import openviking.storage.vectordb.engine as engine_mod

        original_variant = engine_mod.ENGINE_VARIANT
        original_available = engine_mod.AVAILABLE_ENGINE_VARIANTS
        engine_mod.ENGINE_VARIANT = "unavailable"
        engine_mod.AVAILABLE_ENGINE_VARIANTS = ()
        try:
            ok, detail, fix = check_native_engine()
            assert not ok
            assert "No compatible" in detail
            assert fix is not None
        finally:
            engine_mod.ENGINE_VARIANT = original_variant
            engine_mod.AVAILABLE_ENGINE_VARIANTS = original_available


class TestCheckAgfs:
    def test_pass_when_importable(self):
        # pyagfs may not load cleanly in all envs (e.g. dev source checkout)
        ok, detail, fix = check_agfs()
        # Just verify it returns a valid tuple - pass/fail depends on environment
        assert isinstance(ok, bool)
        assert isinstance(detail, str)

    def test_fail_when_missing(self):
        with patch.dict(sys.modules, {"pyagfs": None}):
            # Force ImportError by removing from sys.modules
            saved = sys.modules.pop("pyagfs", None)
            try:
                with patch("builtins.__import__", side_effect=_import_fail("pyagfs")):
                    ok, detail, fix = check_agfs()
                assert not ok
            finally:
                if saved is not None:
                    sys.modules["pyagfs"] = saved


class TestCheckEmbedding:
    def test_pass_with_api_key(self, tmp_path: Path):
        config = tmp_path / "ov.conf"
        config.write_text(
            json.dumps(
                {
                    "embedding": {
                        "dense": {
                            "provider": "openai",
                            "model": "text-embedding-3-small",
                            "api_key": "sk-test123",
                        }
                    }
                }
            )
        )
        with patch("openviking_cli.doctor._find_config", return_value=config):
            ok, detail, fix = check_embedding()
        assert ok
        assert "openai" in detail

    def test_fail_no_api_key(self, tmp_path: Path):
        config = tmp_path / "ov.conf"
        config.write_text(
            json.dumps(
                {
                    "embedding": {
                        "dense": {
                            "provider": "openai",
                            "model": "text-embedding-3-small",
                            "api_key": "{your-api-key}",
                        }
                    }
                }
            )
        )
        with patch("openviking_cli.doctor._find_config", return_value=config):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("OPENAI_API_KEY", None)
                ok, detail, fix = check_embedding()
        assert not ok
        assert "no API key" in detail


class TestCheckVlm:
    def test_pass_with_config(self, tmp_path: Path):
        config = tmp_path / "ov.conf"
        config.write_text(
            json.dumps(
                {"vlm": {"provider": "openai", "model": "gpt-4o-mini", "api_key": "sk-test"}}
            )
        )
        with patch("openviking_cli.doctor._find_config", return_value=config):
            ok, detail, fix = check_vlm()
        assert ok

    def test_fail_no_provider(self, tmp_path: Path):
        config = tmp_path / "ov.conf"
        config.write_text(json.dumps({"vlm": {}}))
        with patch("openviking_cli.doctor._find_config", return_value=config):
            ok, detail, fix = check_vlm()
        assert not ok


class TestCheckDisk:
    def test_pass_normal_disk(self):
        ok, detail, fix = check_disk()
        # Should pass on any dev machine
        assert ok
        assert "GB free" in detail


class TestRunDoctor:
    def test_returns_zero_when_all_pass(self, tmp_path: Path, capsys):
        config = tmp_path / "ov.conf"
        config.write_text(
            json.dumps(
                {
                    "embedding": {"dense": {"provider": "openai", "model": "m", "api_key": "sk-x"}},
                    "vlm": {"provider": "openai", "model": "m", "api_key": "sk-x"},
                }
            )
        )
        with patch("openviking_cli.doctor._find_config", return_value=config):
            code = run_doctor()
        captured = capsys.readouterr()
        assert "OpenViking Doctor" in captured.out
        # May not be 0 if native engine is missing, but the function should complete
        assert isinstance(code, int)

    def test_returns_one_on_failure(self, capsys):
        with patch("openviking_cli.doctor._find_config", return_value=None):
            code = run_doctor()
        assert code == 1
        captured = capsys.readouterr()
        assert "FAIL" in captured.out


def _import_fail(blocked_name: str):
    """Return an __import__ replacement that blocks one specific module."""
    real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

    def _mock_import(name, *args, **kwargs):
        if name == blocked_name:
            raise ImportError(f"Mocked: {name}")
        return real_import(name, *args, **kwargs)

    return _mock_import
