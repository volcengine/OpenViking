# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from pathlib import Path


def test_metric_datasource_owns_safe_read_helpers_and_datasource_subclasses_reuse_them():
    # This test reads source files by absolute path; keep it stable across test subdirectories.
    project_root = Path(__file__).resolve().parents[3]

    core_base = (project_root / "openviking" / "metrics" / "core" / "base.py").read_text(
        encoding="utf-8"
    )
    datasource_base = (
        project_root / "openviking" / "metrics" / "datasources" / "base.py"
    ).read_text(encoding="utf-8")
    probes = (project_root / "openviking" / "metrics" / "datasources" / "probes.py").read_text(
        encoding="utf-8"
    )
    encryption = (
        project_root / "openviking" / "metrics" / "datasources" / "encryption.py"
    ).read_text(encoding="utf-8")
    model_usage = (
        project_root / "openviking" / "metrics" / "datasources" / "model_usage.py"
    ).read_text(encoding="utf-8")
    observer_state = (
        project_root / "openviking" / "metrics" / "datasources" / "observer_state.py"
    ).read_text(encoding="utf-8")

    assert "def safe_read(" in core_base
    assert "def safe_read_async(" in core_base
    assert "def best_effort(" not in datasource_base
    assert "def best_effort_async(" not in datasource_base

    assert "safe_value_probe(" in datasource_base
    assert probes.count("safe_value_probe(") >= 1
    assert encryption.count("safe_value_probe(") >= 1

    assert '"available"' in model_usage
    assert '"usage_by_model"' in model_usage
    assert ".as_dict(" in model_usage
    assert ".normalize_str(" in model_usage
    assert ".as_dict(" in observer_state or ".normalize_str(" in observer_state
