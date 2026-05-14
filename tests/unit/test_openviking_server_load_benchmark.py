from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load_benchmark_module():
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "benchmark" / "custom" / "openviking_server_load_benchmark.py"
    spec = importlib.util.spec_from_file_location("openviking_server_load_benchmark", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_parse_cli_process_result_success_and_error():
    mod = load_benchmark_module()

    success = mod.parse_cli_process_result(
        0,
        '{"ok": true, "result": {"session_id": "s1", "task_id": "t1"}}',
        "",
    )
    assert success.success is True
    assert success.result["session_id"] == "s1"
    assert mod.extract_task_id_from_any(success.result) == "t1"

    failure = mod.parse_cli_process_result(
        1,
        '{"ok": false, "error": {"code": "NOT_FOUND", "message": "missing"}}',
        "",
    )
    assert failure.success is False
    assert failure.exception_type == "CliExitError"
    assert "missing" in failure.error_message


def test_cli_subprocess_command_uses_json_and_no_progress(tmp_path):
    mod = load_benchmark_module()
    config = mod.parse_args(
        [
            "--profile",
            "smoke",
            "--adapters",
            "cli-subprocess",
            "--ov-bin",
            "/tmp/ov",
            "--output-dir",
            str(tmp_path / "out"),
            "--local-data-dir",
            str(tmp_path / "data"),
        ]
    )
    adapter = mod.CliSubprocessAdapter(config=config, cli_config_path=tmp_path / "ovcli.conf")

    command = adapter.build_command(["find", "query", "--uri", "viking://resources"])

    assert command[:5] == ["/tmp/ov", "--output", "json", "--compact", "--no-progress"]
    assert command[-3:] == ["query", "--uri", "viking://resources"]


def test_cli_config_payload_matches_current_schema(tmp_path):
    mod = load_benchmark_module()
    config = mod.parse_args(
        [
            "--profile",
            "smoke",
            "--adapters",
            "cli-subprocess",
            "--output-dir",
            str(tmp_path / "out"),
            "--local-data-dir",
            str(tmp_path / "data"),
        ]
    )

    payload = mod.build_cli_config_payload(config)

    assert set(payload) == {
        "url",
        "api_key",
        "account",
        "user",
        "agent_id",
        "timeout",
        "extra_headers",
    }


def test_direct_http_agent_id_avoids_global_config_autoload(tmp_path):
    mod = load_benchmark_module()
    config = mod.parse_args(
        [
            "--profile",
            "smoke",
            "--output-dir",
            str(tmp_path / "out"),
            "--local-data-dir",
            str(tmp_path / "data"),
        ]
    )

    assert config.agent_id is None
    assert mod.direct_http_agent_id(config) == ""


def test_request_summary_rows_calculate_latency_and_success_rate():
    mod = load_benchmark_module()
    events = [
        mod.RequestEvent(
            adapter="sdk",
            scenario="retrieval",
            operation="find",
            started_at="2026-01-01T00:00:00.000Z",
            ended_at="2026-01-01T00:00:00.010Z",
            elapsed_ms_since_run_start=0,
            latency_ms=10,
            success=True,
            status_code=None,
            exception_type=None,
            error_message=None,
        ),
        mod.RequestEvent(
            adapter="sdk",
            scenario="retrieval",
            operation="find",
            started_at="2026-01-01T00:00:00.020Z",
            ended_at="2026-01-01T00:00:00.060Z",
            elapsed_ms_since_run_start=20,
            latency_ms=40,
            success=False,
            status_code=None,
            exception_type="TimeoutError",
            error_message="timeout",
        ),
    ]
    phases = [
        mod.PhaseMetadata(
            adapter="sdk",
            scenario="retrieval",
            started_at="2026-01-01T00:00:00.000Z",
            ended_at="2026-01-01T00:00:02.000Z",
            duration_seconds=2.0,
        )
    ]

    rows = mod.build_request_summary_rows(events=events, phases=phases)
    retrieval = next(row for row in rows if row["scenario"] == "retrieval")

    assert retrieval["requests"] == 2
    assert retrieval["success_rate"] == 50.0
    assert retrieval["qps"] == 1.0
    assert retrieval["p50_ms"] == 25.0
