from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "benchmark" / "locomo" / "vikingbot" / "reimport_failed.py"
    spec = importlib.util.spec_from_file_location("reimport_failed", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_parse_error_log_collects_unique_failed_sessions():
    module = _load_module()
    log_text = """
[2026-04-07 11:41:16] ERROR [conv-30/session_1]: All connection attempts failed
[2026-04-07 11:41:16] ERROR [conv-30/session_2]:
[2026-04-07 11:41:16] ERROR [conv-30/session_2]:
[2026-04-07 11:41:16] ERROR [conv-47/session_17]:
""".strip()

    failed = module.parse_error_log(log_text)

    assert failed == {
        "conv-30": ["session_1", "session_2"],
        "conv-47": ["session_17"],
    }


def test_resolve_sample_indices_maps_sample_ids_to_dataset_indexes():
    module = _load_module()
    dataset = [
        {"sample_id": "conv-26"},
        {"sample_id": "conv-30"},
        {"sample_id": "conv-47"},
    ]

    resolved = module.resolve_sample_indices(dataset, ["conv-47", "conv-30"])

    assert resolved == {"conv-47": 2, "conv-30": 1}


def test_build_reimport_commands_creates_one_command_per_failed_session():
    module = _load_module()
    commands = module.build_reimport_commands(
        python_executable="/usr/bin/python3",
        input_path="test_data/locomo10.json",
        openviking_url="http://localhost:1933",
        sample_indices={"conv-30": 1},
        failed_sessions={"conv-30": ["session_2", "session_5"]},
        parallel=3,
    )

    assert commands == [
        [
            "/usr/bin/python3",
            "benchmark/locomo/vikingbot/import_to_ov.py",
            "--input",
            "test_data/locomo10.json",
            "--sample",
            "1",
            "--sessions",
            "2",
            "--openviking-url",
            "http://localhost:1933",
            "--parallel",
            "3",
            "--force-ingest",
        ],
        [
            "/usr/bin/python3",
            "benchmark/locomo/vikingbot/import_to_ov.py",
            "--input",
            "test_data/locomo10.json",
            "--sample",
            "1",
            "--sessions",
            "5",
            "--openviking-url",
            "http://localhost:1933",
            "--parallel",
            "3",
            "--force-ingest",
        ],
    ]
