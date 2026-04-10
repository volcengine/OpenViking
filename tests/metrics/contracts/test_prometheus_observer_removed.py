# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from pathlib import Path


def test_runtime_code_has_no_prometheus_observer_references():
    root = Path(__file__).resolve().parents[2] / "openviking"
    banned = (
        "PrometheusObserver",
        "get_prometheus_observer",
        "set_prometheus_observer",
        "prometheus_observer.py",
    )
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in banned:
            assert needle not in text, f"{needle} still found in {path}"

