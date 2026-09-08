from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from start_adapter import SCRIPT_DIR, load_config, parse_args


def test_config_loader_does_not_import_openviking_before_config_selection() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys; sys.path.insert(0, {str(SCRIPT_DIR)!r}); import start_adapter; "
            "assert 'service_app' not in sys.modules; assert 'openviking' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def write_config(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "service": {"port": 1944, "admin_token": "secret"},
                "platform": {
                    "gateway_base_url": "https://gateway.test",
                    "api_key": "platform-secret",
                    "project_id": "00000000000000000000000000000001",
                    "vaka_request_source": "ark-lx",
                },
                "training_task": {
                    "agent_lane_key": "evolving",
                    "agent_execution": {
                        "contract_id": "ark.viking-rollout",
                        "contract_version": "3",
                        "schema_digest": "sha256:test",
                        "values": {
                            "memory_openviking_target": "ov-ark-test",
                        },
                    },
                },
                "rollout": {"require_messages_for_training": True},
            }
        ),
        encoding="utf-8",
    )


def test_load_config_uses_only_file(tmp_path: Path) -> None:
    path = tmp_path / "adapter.local.json"
    write_config(path)

    config = load_config(path)

    assert config.platform.gateway_base_url == "https://gateway.test"
    assert config.platform.api_key == "platform-secret"
    assert config.platform.vaka_request_source == "ark-lx"
    assert config.training_task.agent_lane_key == "evolving"
    assert config.training_task.agent_execution["contract_id"] == "ark.viking-rollout"
    assert config.rollout.extra_header == {"x-vaka-request-source": "ark-lx"}
    assert config.rollout.idempotency_namespace == "openviking-ark4"
    assert config.service.dataset == "ark4-0"


def test_config_argument_defaults_next_to_start_script(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["start_adapter.py"])

    args = parse_args()

    assert Path(args.config) == SCRIPT_DIR / "adapter_config.local.json"


def test_load_config_rejects_protected_rollout_header(tmp_path: Path) -> None:
    path = tmp_path / "adapter.local.json"
    write_config(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["rollout"]["extra_header"] = {"x-tt-backend": "evolution"}
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="protected header"):
        load_config(path)


def test_load_config_rejects_mismatched_rollout_vaka_source(tmp_path: Path) -> None:
    path = tmp_path / "adapter.local.json"
    write_config(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["rollout"]["extra_header"] = {"x-vaka-request-source": "vaka-agentmemory"}
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="must match platform.vaka_request_source"):
        load_config(path)


def test_load_config_rejects_project_display_name(tmp_path: Path) -> None:
    path = tmp_path / "adapter.local.json"
    write_config(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["platform"]["project_id"] = "ov-ark-test"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="not the project display name"):
        load_config(path)


def test_load_config_resolves_memory_key_and_matching_target(tmp_path: Path) -> None:
    path = tmp_path / "adapter.local.json"
    write_config(path)
    ov_config = tmp_path / "openviking.conf"
    ov_config.write_text(
        json.dumps({"server": {"root_api_key": "local-root-key"}}),
        encoding="utf-8",
    )
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["rollout"]["runtime_params"] = {
        "memory": {
            "enabled": True,
            "mode": "read_only",
            "openviking_target": "ov-ark-test",
        }
    }
    raw["memory_proxy"] = {
        "enabled": True,
        "openviking_config_file": "openviking.conf",
        "openviking_api_key_json_path": "server.root_api_key",
        "openviking_account_id": "local-account",
        "openviking_user_id": "local-user",
    }
    raw["training_task"]["agent_execution"]["values"]["memory_openviking_target"] = "ov-ark-test"
    path.write_text(json.dumps(raw), encoding="utf-8")

    config = load_config(path)

    assert config.memory_proxy.enabled is True
    assert config.memory_proxy.openviking_target == "ov-ark-test"
    assert config.memory_proxy.openviking_api_key == "local-root-key"
    assert config.memory_proxy.openviking_account_id == "local-account"
    assert config.memory_proxy.openviking_user_id == "local-user"
    assert config.memory_proxy.event_log_file == tmp_path / "memory_proxy_events.local.jsonl"


def test_load_config_rejects_mismatched_memory_target(tmp_path: Path) -> None:
    path = tmp_path / "adapter.local.json"
    write_config(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["rollout"]["runtime_params"] = {
        "memory": {"enabled": True, "openviking_target": "target-a"}
    }
    raw["memory_proxy"] = {
        "enabled": True,
        "openviking_target": "target-b",
        "openviking_api_key": "local-user-key",
    }
    raw["training_task"]["agent_execution"]["values"]["memory_openviking_target"] = "target-b"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="must equal"):
        load_config(path)


def test_load_config_rejects_mismatched_task_memory_target(tmp_path: Path) -> None:
    path = tmp_path / "adapter.local.json"
    write_config(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["rollout"]["runtime_params"] = {
        "memory": {"enabled": True, "openviking_target": "target-a"}
    }
    raw["memory_proxy"] = {
        "enabled": True,
        "openviking_target": "target-a",
        "openviking_api_key": "local-user-key",
    }
    raw["training_task"]["agent_execution"]["values"]["memory_openviking_target"] = "target-b"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="training_task.agent_execution"):
        load_config(path)


def write_viking_config(path: Path) -> dict:
    write_config(path)
    raw = json.loads(path.read_text())
    raw["platform"]["vaka_request_source"] = "agentmemory"
    raw["training_task"] = {
        "workflow_id": "ark_viking_external_training",
        "task_body": {"agent": {"execution": {"values": {"model_ep": "default"}}}},
    }
    raw["rollout"]["extra_header"] = {
        "x-tt-sandbox": json.dumps({"multi-agents-md-id": "test-md", "env": {"KEEP": "yes"}})
    }
    raw["rollout"]["runtime_params"] = {
        "memory": {"enabled": True, "openviking_target": "local-test"}
    }
    raw["memory_proxy"] = {"enabled": True, "openviking_api_key": "test-local-key"}
    path.write_text(json.dumps(raw))
    return raw


def test_viking_config_has_one_source_and_no_dummy_execution(tmp_path: Path) -> None:
    path = tmp_path / "adapter.json"
    original = write_viking_config(path)
    config = load_config(path)
    assert config.training_task.agent_execution == {}
    assert config.memory_proxy.openviking_target == "local-test"
    values = config.training_task.task_body["agent"]["execution"]["values"]
    assert values == {"model_ep": "default", "request_source": "agentmemory"}
    assert config.rollout.extra_header["x-vaka-request-source"] == "agentmemory"
    sandbox = json.loads(config.rollout.extra_header["x-tt-sandbox"])
    assert sandbox == {
        "multi-agents-md-id": "test-md",
        "env": {"KEEP": "yes", "VAKA_REQUEST_SOURCE": "agentmemory"},
    }
    assert json.loads(path.read_text()) == original


@pytest.mark.parametrize("location", ["task", "sandbox"])
def test_viking_rejects_conflicting_request_source(tmp_path: Path, location: str) -> None:
    path = tmp_path / "adapter.json"
    raw = write_viking_config(path)
    if location == "task":
        raw["training_task"]["task_body"]["agent"]["execution"]["values"]["request_source"] = "old"
    else:
        raw["rollout"]["extra_header"]["x-tt-sandbox"] = json.dumps(
            {"env": {"VAKA_REQUEST_SOURCE": "old"}}
        )
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="must match platform.vaka_request_source"):
        load_config(path)


@pytest.mark.parametrize("field", ["agent_execution", "agent_lane_key", "agent_id", "evaluator_id"])
def test_viking_rejects_ignored_legacy_fields(tmp_path: Path, field: str) -> None:
    path = tmp_path / "adapter.json"
    raw = write_viking_config(path)
    raw["training_task"][field] = "unused"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="remove ignored fields"):
        load_config(path)


def test_viking_existing_task_without_task_body(tmp_path: Path) -> None:
    path = tmp_path / "adapter.json"
    raw = write_viking_config(path)
    raw["training_task"]["existing_task_id"] = "task-existing"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="exactly one"):
        load_config(path)
    del raw["training_task"]["task_body"]
    path.write_text(json.dumps(raw))
    assert load_config(path).training_task.existing_task_id == "task-existing"


@pytest.mark.parametrize("sandbox", ["bad-json", "[]", '{"env":[]}'])
def test_invalid_sandbox_config(tmp_path: Path, sandbox: str) -> None:
    path = tmp_path / "adapter.json"
    raw = write_viking_config(path)
    raw["rollout"]["extra_header"]["x-tt-sandbox"] = sandbox
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="JSON object"):
        load_config(path)


def test_compact_viking_config_has_no_platform_metadata(tmp_path: Path) -> None:
    path = tmp_path / "adapter.json"
    raw = write_viking_config(path)
    raw["training_task"] = {
        "workflow_id": "ark_viking_external_training",
        "lane_key": "agentmemory",
        "viking_experiment_sets": [
            {"experiment_set_id": 371, "version": "V1", "role": "train"},
            {"experiment_set_id": 372, "version": "V1", "role": "eval"},
        ],
    }
    path.write_text(json.dumps(raw))
    config = load_config(path)
    assert config.training_task.task_body == {}
    assert config.training_task.lane_key == "agentmemory"
    assert len(config.training_task.viking_experiment_sets) == 2
    assert config.training_task.model_ep == ""
    assert config.training_task.experiment_id == ""
    assert config.platform.vaka_request_source == "agentmemory"
    assert json.loads(path.read_text()) == raw

    raw["training_task"]["openviking_version"] = "v-old"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="automatically resolved"):
        load_config(path)
    del raw["training_task"]["openviking_version"]
    raw["training_task"]["existing_task_id"] = "task-existing"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="exactly one"):
        load_config(path)


def test_compact_viking_requires_explicit_lane_and_set_version(tmp_path: Path) -> None:
    path = tmp_path / "adapter.json"
    raw = write_viking_config(path)
    raw["training_task"] = {
        "workflow_id": "ark_viking_external_training",
        "viking_experiment_sets": [
            {"experiment_set_id": 371, "version": "V1", "role": "train"},
            {"experiment_set_id": 372, "version": "V1", "role": "eval"},
        ],
    }
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="lane_key is required"):
        load_config(path)
    raw["training_task"]["lane_key"] = "agentmemory"
    raw["training_task"]["viking_experiment_sets"][0]["version"] = ""
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="version must be explicitly selected"):
        load_config(path)
