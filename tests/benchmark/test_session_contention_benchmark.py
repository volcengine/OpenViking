import importlib.util
import sys
from pathlib import Path

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "benchmark" / "custom" / "session_contention_benchmark.py"
)
SPEC = importlib.util.spec_from_file_location("session_contention_benchmark", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
benchmark = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = benchmark
SPEC.loader.exec_module(benchmark)


def _event(
    *,
    adapter: str = "sdk",
    scenario: str = "retrieval",
    operation: str = "find",
    started_ms: float,
    latency_ms: float,
    success: bool = True,
):
    return benchmark.RequestEvent(
        adapter=adapter,
        scenario=scenario,
        operation=operation,
        started_at="2026-01-01T00:00:00Z",
        ended_at="2026-01-01T00:00:01Z",
        elapsed_ms_since_run_start=started_ms,
        latency_ms=latency_ms,
        success=success,
        status_code=200 if success else 500,
        exception_type=None if success else "RuntimeError",
        error_message=None if success else "failed",
    )


def test_overall_qps_uses_adapter_operation_active_span():
    events = [
        _event(started_ms=0, latency_ms=100),
        _event(started_ms=900, latency_ms=100),
        _event(
            adapter="cli-http",
            operation="search",
            started_ms=10_000,
            latency_ms=1_000,
        ),
    ]

    rows = benchmark.build_request_summary_rows(events=events, phases=[])
    overall = benchmark.find_summary(rows, adapter="sdk", scenario="ALL", operation="find")

    assert overall is not None
    assert overall["qps_span_seconds"] == pytest.approx(1.0)
    assert overall["qps"] == pytest.approx(2.0)
    assert overall["successful_qps"] == pytest.approx(2.0)


def test_summary_separates_success_and_failure_latency():
    events = [
        _event(started_ms=0, latency_ms=100, success=True),
        _event(started_ms=100, latency_ms=300, success=True),
        _event(started_ms=200, latency_ms=5_000, success=False),
    ]

    row = benchmark.build_summary_row("sdk", "mixed", "find", events, 2.0)

    assert row["qps"] == pytest.approx(1.5)
    assert row["successful_qps"] == pytest.approx(1.0)
    assert row["p50_success_ms"] == pytest.approx(200.0)
    assert row["p95_success_ms"] == pytest.approx(290.0)
    assert row["p50_failure_ms"] == pytest.approx(5_000.0)
    assert row["p95_failure_ms"] == pytest.approx(5_000.0)
    assert row["p95_ms"] == pytest.approx(290.0)
    assert row["slow_success_gt_1s"] == 0
    assert row["slow_failure_gt_1s"] == 1


def test_slo_gates_are_disabled_by_default():
    config = benchmark.parse_args(["--profile", "smoke", "--adapters", "sdk"])
    summary_rows = [
        {
            "adapter": "sdk",
            "scenario": "ALL",
            "operation": "find",
            "requests": 10,
            "failures": 10,
            "success_rate": 0.0,
            "p95_success_ms": None,
        }
    ]

    assert config.max_failure_rate_percent is None
    assert config.max_success_p95_ms is None
    assert benchmark.evaluate_exit_gates(config, summary_rows) == []


def test_opt_in_slo_gates_report_failure_rate_and_success_latency():
    config = benchmark.parse_args(
        [
            "--profile",
            "smoke",
            "--adapters",
            "sdk",
            "--max-failure-rate-percent",
            "5",
            "--max-success-p95-ms",
            "250",
        ]
    )
    summary_rows = [
        {
            "adapter": "sdk",
            "scenario": "ALL",
            "operation": "find",
            "requests": 100,
            "failures": 10,
            "success_rate": 90.0,
            "p95_success_ms": 300.0,
        },
        {
            "adapter": "sdk",
            "scenario": "retrieval",
            "operation": "find",
            "requests": 100,
            "failures": 10,
            "success_rate": 90.0,
            "p95_success_ms": 300.0,
        },
    ]

    violations = benchmark.evaluate_exit_gates(config, summary_rows)

    assert violations == [
        "sdk/find failure rate 10.0000% exceeds 5.0000%",
        "sdk/find successful p95 300.0000ms exceeds 250.0000ms",
    ]


@pytest.mark.asyncio
async def test_runner_returns_nonzero_when_opt_in_gate_fails(tmp_path, monkeypatch):
    config = benchmark.parse_args(
        [
            "--profile",
            "smoke",
            "--adapters",
            "sdk",
            "--output-dir",
            str(tmp_path),
            "--max-failure-rate-percent",
            "0",
        ]
    )
    runner = benchmark.BenchmarkRunner(config)

    async def no_op():
        return None

    async def create_no_adapters():
        return {}

    monkeypatch.setattr(runner, "_prepare_filesystem", no_op)
    monkeypatch.setattr(runner, "_prepare_remote_state", no_op)
    monkeypatch.setattr(runner, "_create_adapters", create_no_adapters)
    monkeypatch.setattr(runner, "_write_outputs", lambda: ["sdk/find gate failed"])
    monkeypatch.setattr(runner, "_print_summary_path", lambda: None)

    assert await runner.run() == 1
