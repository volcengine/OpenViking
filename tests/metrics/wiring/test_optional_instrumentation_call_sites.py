# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from pathlib import Path


def test_optional_cache_datasource_instrumentation_is_wired_into_key_call_sites():
    # This test reads source files by absolute path; keep it stable across test subdirectories.
    project_root = Path(__file__).resolve().parents[3]
    targets = [
        project_root / "openviking" / "storage" / "queuefs" / "semantic_dag.py",
        project_root / "openviking" / "storage" / "queuefs" / "semantic_processor.py",
        project_root / "openviking" / "session" / "memory" / "extract_loop.py",
    ]
    missing = []
    for path in targets:
        text = path.read_text(encoding="utf-8")
        if "CacheEventDataSource.record_" not in text:
            missing.append(path.name)
    assert missing == []
