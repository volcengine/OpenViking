# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Build usage reporter instances from server config."""

from __future__ import annotations

import importlib
from typing import Any

from openviking.server.config import UsageReporterConfig, UsageReporterSinkConfig

from .extractors import MemoryUsageExtractor
from .reporter import UsageReporter
from .sinks import UsageSink


def _load_class(class_path: str) -> type:
    module_name, class_name = class_path.rsplit(".", 1)
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    if not isinstance(cls, type):
        raise TypeError(f"{class_path} does not resolve to a class")
    return cls


def _build_sink(config: UsageReporterSinkConfig) -> UsageSink:
    if config.type == "custom":
        if not config.class_path:
            raise ValueError("custom usage sink requires class_path")
        cls = _load_class(config.class_path)
        kwargs: dict[str, Any] = dict(config.config)
        return cls(**kwargs)

    raise ValueError(f"Unsupported usage sink type: {config.type}")


def build_usage_reporter(config: UsageReporterConfig) -> UsageReporter | None:
    if not config.enabled:
        return None

    extractors = []
    for name in config.extractors:
        if name == "memory_usage":
            extractors.append(MemoryUsageExtractor())
        else:
            raise ValueError(f"Unsupported usage extractor: {name}")

    sinks = [_build_sink(sink_config) for sink_config in config.sinks]
    return UsageReporter(
        extractors=extractors,
        sinks=sinks,
    )
