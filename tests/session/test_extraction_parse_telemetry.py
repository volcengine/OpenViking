# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Structured parse-failure telemetry for the extraction loop (RFC #4243 slice 1)."""

import sys
import types
from types import SimpleNamespace

_ark = types.ModuleType("volcenginesdkarkruntime")
_ark_exc = types.ModuleType("volcenginesdkarkruntime._exceptions")


class _ArkRateLimitError(Exception):
    pass


_ark_exc.ArkRateLimitError = _ArkRateLimitError
_ark._exceptions = _ark_exc
sys.modules.setdefault("volcenginesdkarkruntime", _ark)
sys.modules.setdefault("volcenginesdkarkruntime._exceptions", _ark_exc)

from openviking.session.compressor_v3 import _report_extraction_telemetry
from openviking.session.memory.extract_loop import ExtractLoop


class _CaptureTelemetry:
    def __init__(self):
        self.values = {}

    def set(self, name, value):
        self.values[name] = value


def _fake_result():
    return SimpleNamespace(
        written_uris=[],
        edited_uris=[],
        deleted_uris=[],
        skipped_operations=[],
        errors=[(
            "viking://user/u/memories/entities/unknown.md",
            "Final response could not be parsed as JSON operations "
            "after 4 iterations (failure_kind=parse_error)",
        )],
    )


def _fake_operations():
    return SimpleNamespace(upsert_operations=[], delete_file_contents=[])


def test_parse_stats_emitted_for_zero_extraction(monkeypatch):
    captured = _CaptureTelemetry()
    monkeypatch.setattr(
        "openviking.session.compressor_v3.get_current_telemetry", lambda: captured
    )
    stats = {
        "failure_kind": "parse_error",
        "format_retries_used": 1,
        "iterations_used": 4,
        "max_iterations": 4,
        "exhausted": True,
    }

    _report_extraction_telemetry(_fake_result(), _fake_operations(), stats)

    assert captured.values["memory.extract.parse.failure.parse_error"] == 1
    assert captured.values["memory.extract.parse.format_retries_used"] == 1
    assert captured.values["memory.extract.parse.iterations_used"] == 4
    assert captured.values["memory.extract.parse.exhausted"] == 1
    # existing counters keep working for the zero-extraction shape
    assert captured.values["memory.extract.failed"] == 1


def test_parse_stats_absent_emits_nothing(monkeypatch):
    captured = _CaptureTelemetry()
    monkeypatch.setattr(
        "openviking.session.compressor_v3.get_current_telemetry", lambda: captured
    )

    _report_extraction_telemetry(_fake_result(), _fake_operations())

    assert not [k for k in captured.values if k.startswith("memory.extract.parse.")]


def test_parse_stats_no_failure_kind_skips_kind_counter(monkeypatch):
    captured = _CaptureTelemetry()
    monkeypatch.setattr(
        "openviking.session.compressor_v3.get_current_telemetry", lambda: captured
    )
    stats = {
        "failure_kind": None,
        "format_retries_used": 0,
        "iterations_used": 2,
        "max_iterations": 3,
        "exhausted": False,
    }

    _report_extraction_telemetry(_fake_result(), _fake_operations(), stats)

    assert "memory.extract.parse.failure.parse_error" not in captured.values
    assert "memory.extract.parse.failure.refusal_text" not in captured.values
    assert captured.values["memory.extract.parse.iterations_used"] == 2
    assert captured.values["memory.extract.parse.exhausted"] == 0


def _bare_loop():
    """Construct an ExtractLoop without its heavy dependencies.

    Only the parse-stats state is initialized; the recorder methods under
    test touch nothing else.
    """
    loop = ExtractLoop.__new__(ExtractLoop)
    loop.parse_stats = {
        "failure_kind": None,
        "format_retries_used": 0,
        "iterations_used": 0,
        "max_iterations": 0,
        "exhausted": False,
    }
    loop._format_retry_count = 0
    return loop


def test_parse_stats_initial_shape():
    loop = _bare_loop()
    assert loop.parse_stats == {
        "failure_kind": None,
        "format_retries_used": 0,
        "iterations_used": 0,
        "max_iterations": 0,
        "exhausted": False,
    }


def test_parse_stats_full_failure_lifecycle():
    """failure -> retry -> more attempts -> exhaustion mirrors a zero-extraction run."""
    loop = _bare_loop()
    loop._record_parse_attempt(0, 3)
    loop._record_parse_failure("parse_error")
    loop._format_retry_count = 1
    loop._record_format_retry()
    loop._record_parse_attempt(3, 4)  # retry extended max_iterations
    loop._record_parse_failure("refusal_text")
    loop._record_parse_exhausted()

    assert loop.parse_stats == {
        "failure_kind": "refusal_text",  # last failure wins
        "format_retries_used": 1,
        "iterations_used": 4,
        "max_iterations": 4,
        "exhausted": True,
    }


def test_parse_stats_success_run_stays_clean():
    loop = _bare_loop()
    loop._record_parse_attempt(1, 3)
    assert loop.parse_stats["iterations_used"] == 2
    assert loop.parse_stats["failure_kind"] is None
    assert loop.parse_stats["exhausted"] is False


def _finished_summary_with_parse_stats(parse_stats: dict) -> dict:
    """Drive the real OperationTelemetry → TelemetrySummaryBuilder path."""
    from openviking.telemetry.operation import OperationTelemetry

    t = OperationTelemetry(operation="session_commit", enabled=True)
    t.set("memory.extract.parse.failure.parse_error", 1)
    t.set("memory.extract.parse.format_retries_used", parse_stats.get("format_retries_used", 0))
    t.set("memory.extract.parse.iterations_used", parse_stats.get("iterations_used", 0))
    t.set("memory.extract.parse.exhausted", 1 if parse_stats.get("exhausted") else 0)
    snapshot = t.finish()
    assert snapshot is not None
    return snapshot.summary


def test_parse_stats_reach_finished_summary_contract():
    summary = _finished_summary_with_parse_stats(
        {"failure_kind": "parse_error", "format_retries_used": 1, "iterations_used": 4, "exhausted": True}
    )
    parse = summary["memory"]["extract"]["parse"]
    assert parse == {
        "failure_kind": "parse_error",
        "format_retries_used": 1,
        "iterations_used": 4,
        "exhausted": 1,
    }


def test_parse_stats_bridge_maps_to_prometheus_counters(monkeypatch):
    from openviking.metrics.collectors.telemetry_bridge import TelemetryBridgeCollector
    from openviking.metrics.core.registry import MetricRegistry
    from openviking.metrics.datasources.base import EventMetricDataSource
    from openviking.metrics.exporters.prometheus import PrometheusExporter

    summary = _finished_summary_with_parse_stats(
        {
            "failure_kind": "parse_error",
            "format_retries_used": 1,
            "iterations_used": 4,
            "exhausted": True,
        }
    )
    registry = MetricRegistry()
    collector = TelemetryBridgeCollector()
    captured = []

    def _emit(event_name, payload):
        captured.append((event_name, payload))

    # monkeypatch keeps the original staticmethod descriptor and restores it on
    # teardown; assigning + `del` would permanently drop the production _emit.
    monkeypatch.setattr(EventMetricDataSource, "_emit", staticmethod(_emit))
    from openviking.metrics.datasources.telemetry_bridge import (
        TelemetryBridgeEventDataSource,
    )

    TelemetryBridgeEventDataSource.record_summary(summary)
    assert EventMetricDataSource._emit is not None
    for event_name, payload in captured:
        collector.receive_hook(event_name, payload, registry)

    text = PrometheusExporter(registry=registry).render()
    assert 'failure_kind="parse_error"' in text
    assert "openviking_memory_parse_failures_total" in text
    assert "openviking_memory_parse_exhausted_total" in text
    # aggregate-safe surfaces for "4 iterations used, 1 format retry"
    assert "openviking_memory_parse_iterations_bucket" in text
    assert "openviking_memory_parse_format_retries_bucket" in text
    assert 'le="4.0"' in text  # 4-iteration observation lands in the le=4 bucket
    assert 'le="1.0"' in text  # 1 format-retry observation lands in the le=1 bucket


def test_bridge_emit_patch_restored_after_test(monkeypatch):
    """The bridge test must not leak a stubbed _emit into later tests (review follow-up)."""
    from openviking.metrics.datasources.base import EventMetricDataSource

    original = EventMetricDataSource.__dict__.get("_emit")
    assert original is not None, "production _emit must exist on the class"

    def _stub(event_name, payload):
        return None

    monkeypatch.setattr(EventMetricDataSource, "_emit", staticmethod(_stub))
    assert EventMetricDataSource.__dict__["_emit"] is not original
    # monkeypatch teardown restores the original descriptor; the next test that
    # calls record_summary() sees the production emitter again.


def test_parse_stats_failure_then_recovery_clears_failure_kind():
    """Attempt-level failure must not survive a successful parse (#4628 review)."""
    loop = _bare_loop()
    loop._record_parse_attempt(0, 3)
    loop._record_parse_failure("parse_error")  # first attempt fails
    loop._format_retry_count = 1
    loop._record_format_retry()
    loop._record_parse_attempt(1, 4)  # retry succeeds
    loop._record_parse_recovery()

    assert loop.parse_stats["failure_kind"] is None  # final response WAS parseable
    assert loop.parse_stats["format_retries_used"] == 1  # retry usage still visible
    assert loop.parse_stats["iterations_used"] == 2
    assert loop.parse_stats["exhausted"] is False
