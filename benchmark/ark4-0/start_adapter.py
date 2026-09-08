#!/usr/bin/env python3
"""Start the standalone Ark platform adapter for OpenViking batch training."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import uvicorn

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from memory_proxy import MemoryProxyConfig  # noqa: E402
from platform_client import PlatformClientConfig, TrainingPlatformClient  # noqa: E402
from task_request import validate_sets  # noqa: E402

_PROTECTED_ROLLOUT_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "host",
        "content-length",
        "content-type",
        "connection",
        "proxy-connection",
        "keep-alive",
        "transfer-encoding",
        "upgrade",
        "x-admin-token",
        "x-homepage-api-key",
        "x-user-id",
        "x-tt-env",
    }
)
_PROJECT_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")


@dataclass(frozen=True, slots=True)
class ServiceSettings:
    host: str = "127.0.0.1"
    port: int = 1944
    dataset: str = "ark4-0"
    domain: str = "ark"
    rollout_concurrency: int = 16
    admin_token: str = ""
    log_level: str = "info"


@dataclass(frozen=True, slots=True)
class PlatformSettings:
    gateway_base_url: str
    api_key: str = ""
    project_id: str = ""
    vaka_request_source: str = "ark-lx"
    headers: dict[str, str] = field(default_factory=dict)
    request_timeout_seconds: float = 120.0


@dataclass(frozen=True, slots=True)
class TrainingTaskSettings:
    task_name: str = "openviking_ark4_external_training"
    workflow_id: str = "ov_external_training"
    agent_id: str = "ark"
    lane_key: str = ""
    rollout_resource_id: str = ""
    agent_execution: dict[str, Any] = field(default_factory=dict)
    evaluator_id: str = "rollout_builtin@v1"
    task_body: dict[str, Any] = field(default_factory=dict)
    viking_experiment_sets: list[dict[str, Any]] = field(default_factory=list)
    model_ep: str = ""
    experiment_id: str = ""
    existing_task_id: str = ""
    ready_poll_interval_seconds: float = 2.0
    ready_timeout_seconds: float = 900.0


@dataclass(frozen=True, slots=True)
class RolloutSettings:
    poll_interval_seconds: float = 2.0
    timeout_seconds: float = 3600.0
    connector_config: dict[str, Any] = field(default_factory=dict)
    runtime_params: dict[str, Any] = field(default_factory=dict)
    extra_header: dict[str, Any] = field(default_factory=dict)
    idempotency_namespace: str = "openviking-ark4"
    require_messages_for_training: bool = True


@dataclass(frozen=True, slots=True)
class AdapterFileConfig:
    path: Path
    service: ServiceSettings
    platform: PlatformSettings
    training_task: TrainingTaskSettings
    rollout: RolloutSettings
    memory_proxy: MemoryProxyConfig


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Start the standalone Ark adapter consumed by OpenViking's native "
            "run_batch_train_eval command."
        )
    )
    parser.add_argument(
        "--config",
        default=str(SCRIPT_DIR / "adapter_config.local.json"),
        help=(
            "Path to adapter JSON configuration "
            "(default: adapter_config.local.json next to this script)"
        ),
    )
    return parser.parse_args()


def load_config(path: str | Path) -> AdapterFileConfig:
    config_path = Path(path).expanduser().resolve()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"adapter config does not exist: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"adapter config is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("adapter config root must be a JSON object")

    service_raw = _object(raw, "service")
    platform_raw = _object(raw, "platform")
    task_raw = _object(raw, "training_task")
    rollout_raw = _object(raw, "rollout")
    memory_raw = _object(raw, "memory_proxy")
    platform_headers = _any_dict(platform_raw.get("headers"), "platform.headers")
    extra_header = _any_dict(rollout_raw.get("extra_header"), "rollout.extra_header")
    for label, headers in (
        ("platform.headers", platform_headers),
        ("rollout.extra_header", extra_header),
    ):
        if any(str(key).strip().lower() == "x-tt-backend" for key in headers):
            raise ValueError(
                f"{label}.x-tt-backend is removed; use training_task.lane_key "
                "for scheduling.lane_key when creating a Task"
            )
    removed_lane_fields = {"agent_lane_id", "agent_lane_key"} & task_raw.keys()
    if removed_lane_fields:
        raise ValueError(
            "training_task fields removed: "
            + ", ".join(sorted(removed_lane_fields))
            + "; use training_task.lane_key and rollout_resource_id"
        )
    if task_raw.get("workflow_id") == "ark_viking_external_training":
        unused = {"agent_execution", "evaluator_id"} & task_raw.keys()
        if unused:
            raise ValueError(
                "Viking workflow uses training_task.task_body; remove ignored fields: "
                + ", ".join(sorted(unused))
            )

    service = ServiceSettings(
        host=_text(service_raw.get("host"), "service.host", default="127.0.0.1"),
        port=_integer(service_raw.get("port"), "service.port", default=1944, minimum=1),
        dataset=_text(service_raw.get("dataset"), "service.dataset", default="ark4-0"),
        domain=_text(service_raw.get("domain"), "service.domain", default="ark"),
        rollout_concurrency=_integer(
            service_raw.get("rollout_concurrency"),
            "service.rollout_concurrency",
            default=16,
            minimum=1,
        ),
        admin_token=_optional_text(service_raw.get("admin_token")),
        log_level=_text(service_raw.get("log_level"), "service.log_level", default="info"),
    )
    if service.port > 65535:
        raise ValueError("service.port must be <= 65535")

    project_id = _optional_text(platform_raw.get("project_id"))
    _validate_project_id(project_id)
    platform = PlatformSettings(
        gateway_base_url=_text(
            platform_raw.get("gateway_base_url"),
            "platform.gateway_base_url",
        ),
        api_key=_optional_text(platform_raw.get("api_key")),
        project_id=project_id,
        vaka_request_source=_text(
            platform_raw.get("vaka_request_source"),
            "platform.vaka_request_source",
            default="ark-lx",
        ),
        headers=_string_dict(
            platform_headers,
            "platform.headers",
        ),
        request_timeout_seconds=_number(
            platform_raw.get("request_timeout_seconds"),
            "platform.request_timeout_seconds",
            default=120.0,
            minimum=0.001,
        ),
    )
    task_body = _any_dict(task_raw.get("task_body"), "training_task.task_body")
    agent_id = _identifier(task_raw, "agent_id", default="ark")
    lane_key = _identifier(task_raw, "lane_key")
    rollout_resource_id = _identifier(task_raw, "rollout_resource_id")
    if bool(lane_key) != bool(rollout_resource_id):
        raise ValueError("training_task.lane_key and rollout_resource_id must be configured together")
    experiment_sets = (
        validate_sets(task_raw["viking_experiment_sets"])
        if "viking_experiment_sets" in task_raw
        else []
    )
    if experiment_sets:
        if task_raw.get("workflow_id") != "ark_viking_external_training":
            raise ValueError("viking_experiment_sets requires ark_viking_external_training")
        if not lane_key:
            raise ValueError("training_task.lane_key and rollout_resource_id are required")
        unknown = task_raw.keys() - {
            "workflow_id",
            "task_name",
            "agent_id",
            "lane_key",
            "rollout_resource_id",
            "viking_experiment_sets",
            "model_ep",
            "experiment_id",
            "ready_poll_interval_seconds",
            "ready_timeout_seconds",
            "task_body",
            "existing_task_id",
        }
        if unknown:
            raise ValueError(
                "remove automatically resolved/unknown training_task fields: "
                + ", ".join(sorted(unknown))
            )
    if task_raw.get("workflow_id") == "ark_viking_external_training":
        if (
            sum(
                (
                    bool(task_body),
                    bool(experiment_sets),
                    bool(_optional_text(task_raw.get("existing_task_id"))),
                )
            )
            != 1
        ):
            raise ValueError(
                "configure exactly one of training_task.viking_experiment_sets, "
                "task_body and existing_task_id"
            )
        if task_body:
            is_v2 = bool(str(task_body.get("schema_version") or ""))
            if not is_v2 and "agent" in task_body:
                raise ValueError(
                    "task_body.agent requires schema_version=training-task-request.v2; "
                    "or use flat task_body.agent_execution without an agent object"
                )
            if is_v2:
                agent = _any_dict(task_body.get("agent"), "task_body.agent")
                execution = _any_dict(agent.get("execution"), "task_body.agent.execution")
            else:
                execution = _any_dict(task_body.get("agent_execution"), "task_body.agent_execution")
            values = _any_dict(execution.get("values"), "task_body execution.values")
            _set_request_source(values, "request_source", platform.vaka_request_source, "task_body")
            execution["values"] = values
            if is_v2:
                agent["execution"] = execution
                task_body["agent"] = agent
            else:
                task_body["agent_execution"] = execution
    training_task = TrainingTaskSettings(
        task_name=_text(
            task_raw.get("task_name"),
            "training_task.task_name",
            default="openviking_ark4_external_training",
        ),
        workflow_id=_text(
            task_raw.get("workflow_id"),
            "training_task.workflow_id",
            default="ov_external_training",
        ),
        agent_id=agent_id,
        lane_key=lane_key,
        rollout_resource_id=rollout_resource_id,
        agent_execution=_any_dict(
            task_raw.get("agent_execution"),
            "training_task.agent_execution",
        ),
        evaluator_id=_text(
            task_raw.get("evaluator_id"),
            "training_task.evaluator_id",
            default="rollout_builtin@v1",
        ),
        task_body=task_body,
        viking_experiment_sets=experiment_sets,
        model_ep=_optional_text(task_raw.get("model_ep")),
        experiment_id=_optional_text(task_raw.get("experiment_id")),
        existing_task_id=_optional_text(task_raw.get("existing_task_id")),
        ready_poll_interval_seconds=_number(
            task_raw.get("ready_poll_interval_seconds"),
            "training_task.ready_poll_interval_seconds",
            default=2.0,
            minimum=0.001,
        ),
        ready_timeout_seconds=_number(
            task_raw.get("ready_timeout_seconds"),
            "training_task.ready_timeout_seconds",
            default=900.0,
            minimum=0.001,
        ),
    )
    _validate_extra_header(extra_header)
    vaka_header_keys = [key for key in extra_header if str(key).lower() == "x-vaka-request-source"]
    for key in vaka_header_keys:
        if str(extra_header[key]).strip() != platform.vaka_request_source:
            raise ValueError(
                "rollout.extra_header.x-vaka-request-source must match platform.vaka_request_source"
            )
        del extra_header[key]
    extra_header["x-vaka-request-source"] = platform.vaka_request_source
    for key in extra_header:
        if key.lower() != "x-tt-sandbox":
            continue
        try:
            sandbox = json.loads(extra_header[key])
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("rollout.extra_header.x-tt-sandbox must be a JSON object") from exc
        if not isinstance(sandbox, dict):
            raise ValueError("rollout.extra_header.x-tt-sandbox must be a JSON object")
        env = sandbox.setdefault("env", {})
        if not isinstance(env, dict):
            raise ValueError("x-tt-sandbox.env must be a JSON object")
        _set_request_source(env, "VAKA_REQUEST_SOURCE", platform.vaka_request_source, "sandbox.env")
        extra_header[key] = json.dumps(sandbox, ensure_ascii=False)
    rollout = RolloutSettings(
        poll_interval_seconds=_number(
            rollout_raw.get("poll_interval_seconds"),
            "rollout.poll_interval_seconds",
            default=2.0,
            minimum=0.001,
        ),
        timeout_seconds=_number(
            rollout_raw.get("timeout_seconds"),
            "rollout.timeout_seconds",
            default=3600.0,
            minimum=0.001,
        ),
        connector_config=_any_dict(
            rollout_raw.get("connector_config"),
            "rollout.connector_config",
        ),
        runtime_params=_any_dict(rollout_raw.get("runtime_params"), "rollout.runtime_params"),
        extra_header=extra_header,
        idempotency_namespace=_text(
            rollout_raw.get("idempotency_namespace"),
            "rollout.idempotency_namespace",
            default="openviking-ark4",
        ),
        require_messages_for_training=_boolean(
            rollout_raw.get("require_messages_for_training"),
            "rollout.require_messages_for_training",
            default=True,
        ),
    )
    memory_enabled = _boolean(
        memory_raw.get("enabled"),
        "memory_proxy.enabled",
        default=False,
    )
    runtime_memory = _any_dict(
        rollout.runtime_params.get("memory"),
        "rollout.runtime_params.memory",
    )
    memory_target = _optional_text(
        memory_raw.get("openviking_target") or runtime_memory.get("openviking_target")
    )
    event_log_value = _optional_text(memory_raw.get("event_log_file"))
    event_log_file = _relative_path(
        event_log_value or "memory_proxy_events.local.jsonl",
        base=config_path.parent,
    )
    openviking_api_key = _optional_text(memory_raw.get("openviking_api_key"))
    key_file_value = _optional_text(memory_raw.get("openviking_config_file"))
    if not openviking_api_key and key_file_value:
        key_file = _relative_path(key_file_value, base=config_path.parent)
        key_path = _text(
            memory_raw.get("openviking_api_key_json_path"),
            "memory_proxy.openviking_api_key_json_path",
            default="ov_server.api_key",
        )
        openviking_api_key = _read_json_text(key_file, key_path)
    memory_proxy = MemoryProxyConfig(
        enabled=memory_enabled,
        openviking_target=memory_target,
        openviking_url=_text(
            memory_raw.get("openviking_url"),
            "memory_proxy.openviking_url",
            default="http://127.0.0.1:1933",
        ),
        openviking_api_key=openviking_api_key,
        openviking_account_id=_text(
            memory_raw.get("openviking_account_id"),
            "memory_proxy.openviking_account_id",
            default="default",
        ),
        openviking_user_id=_text(
            memory_raw.get("openviking_user_id"),
            "memory_proxy.openviking_user_id",
            default="default",
        ),
        timeout_seconds=_number(
            memory_raw.get("timeout_seconds"),
            "memory_proxy.timeout_seconds",
            default=180.0,
            minimum=0.001,
        ),
        event_log_file=event_log_file,
    )
    _validate_runtime_memory_target(rollout.runtime_params, memory_proxy)
    # Viking gets its authoritative identity from the platform manifest. Its
    # callback target is sent in runtime_params, not legacy agent_execution.
    if training_task.workflow_id != "ark_viking_external_training":
        _validate_task_memory_target(training_task.agent_execution, memory_proxy)
    return AdapterFileConfig(
        path=config_path,
        service=service,
        platform=platform,
        training_task=training_task,
        rollout=rollout,
        memory_proxy=memory_proxy,
    )


async def run(config: AdapterFileConfig) -> None:
    # Load the configured local OV settings before importing the train modules,
    # whose loggers otherwise initialize from an unrelated default ov.conf.
    raw = json.loads(config.path.read_text(encoding="utf-8"))
    ov_path = raw.get("memory_proxy", {}).get("openviking_config_file")
    if ov_path:
        os.environ["OPENVIKING_CONFIG_FILE"] = str(_relative_path(ov_path, base=config.path.parent))
    from service_app import ArkAdapterServiceConfig, create_app

    client_config = PlatformClientConfig(
        gateway_base_url=config.platform.gateway_base_url,
        api_key=config.platform.api_key or None,
        project_id=config.platform.project_id or None,
        vaka_request_source=config.platform.vaka_request_source,
        headers=config.platform.headers,
        timeout_seconds=config.platform.request_timeout_seconds,
    )
    async with TrainingPlatformClient(client_config) as client:
        app = create_app(
            client=client,
            config=ArkAdapterServiceConfig(
                dataset=config.service.dataset,
                domain=config.service.domain,
                task_name=config.training_task.task_name,
                workflow_id=config.training_task.workflow_id,
                agent_id=config.training_task.agent_id,
                lane_key=config.training_task.lane_key,
                rollout_resource_id=config.training_task.rollout_resource_id,
                agent_execution=config.training_task.agent_execution,
                evaluator_id=config.training_task.evaluator_id,
                task_body=config.training_task.task_body,
                viking_experiment_sets=config.training_task.viking_experiment_sets,
                model_ep=config.training_task.model_ep,
                experiment_id=config.training_task.experiment_id,
                request_source=config.platform.vaka_request_source,
                existing_task_id=config.training_task.existing_task_id,
                task_ready_poll_interval_seconds=(config.training_task.ready_poll_interval_seconds),
                task_ready_timeout_seconds=config.training_task.ready_timeout_seconds,
                rollout_concurrency=config.service.rollout_concurrency,
                rollout_poll_interval_seconds=config.rollout.poll_interval_seconds,
                rollout_timeout_seconds=config.rollout.timeout_seconds,
                connector_config=config.rollout.connector_config,
                runtime_params=config.rollout.runtime_params,
                extra_header=config.rollout.extra_header,
                idempotency_namespace=config.rollout.idempotency_namespace,
                require_messages_for_training=config.rollout.require_messages_for_training,
                admin_token=config.service.admin_token,
                memory_proxy=config.memory_proxy,
            ),
        )
        server_url = f"http://{config.service.host}:{config.service.port}"
        print(f"[ark4-adapter] listening at {server_url}", flush=True)
        print(f"[ark4-adapter] config: {config.path}", flush=True)
        print(
            "[ark4-adapter] workflow: "
            + config.training_task.workflow_id
            + "; "
            + (
                "attach configured platform task"
                if config.training_task.existing_task_id
                else "create a platform task for each run_batch_train_eval invocation"
            ),
            flush=True,
        )
        print(
            f"[ark4-adapter] OpenViking argument: --benchmark-service-url {server_url}",
            flush=True,
        )
        if config.memory_proxy.enabled:
            print(
                "[ark4-adapter] memory callback ready: "
                f"target={config.memory_proxy.openviking_target} "
                f"local={server_url}/api/v1/search/find",
                flush=True,
            )
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=config.service.host,
                port=config.service.port,
                log_level=config.service.log_level,
            )
        )
        await server.serve()


def _set_request_source(values: dict[str, Any], key: str, source: str, label: str) -> None:
    if key in values and str(values[key]).strip() != source:
        raise ValueError(f"{label}.{key} must match platform.vaka_request_source")
    values[key] = source


def _identifier(task: dict[str, Any], key: str, *, default: str = "") -> str:
    if key not in task:
        return default
    value = task[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"training_task.{key} must be a non-empty string")
    if any(character.isspace() for character in value.strip()):
        raise ValueError(f"training_task.{key} must not contain whitespace")
    return value.strip()


def _object(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a JSON object")
    return dict(value)


def _text(value: Any, label: str, *, default: str | None = None) -> str:
    if value is None and default is not None:
        value = default
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    return text


def _optional_text(value: Any) -> str:
    return str(value or "").strip()


def _integer(value: Any, label: str, *, default: int, minimum: int) -> int:
    if value is None:
        value = default
    if isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc
    if parsed < minimum:
        raise ValueError(f"{label} must be >= {minimum}")
    return parsed


def _number(value: Any, label: str, *, default: float, minimum: float) -> float:
    if value is None:
        value = default
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a number") from exc
    if parsed < minimum:
        raise ValueError(f"{label} must be >= {minimum}")
    return parsed


def _boolean(value: Any, label: str, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise ValueError(f"{label} must be a boolean")


def _any_dict(value: Any, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return dict(value)


def _string_dict(value: Any, label: str) -> dict[str, str]:
    raw = _any_dict(value, label)
    return {str(key): str(item) for key, item in raw.items()}


def _validate_extra_header(headers: dict[str, Any]) -> None:
    for raw_key, raw_value in headers.items():
        key = str(raw_key).strip()
        value = str(raw_value)
        if not key:
            raise ValueError("rollout.extra_header keys must be non-empty")
        if "\r" in key or "\n" in key or "\r" in value or "\n" in value:
            raise ValueError("rollout.extra_header must not contain CR/LF")
        if key.lower() in _PROTECTED_ROLLOUT_HEADERS:
            raise ValueError(
                f"rollout.extra_header cannot override protected header {key!r}; "
                "use the platform Task binding for lane headers"
            )


def _validate_project_id(project_id: str) -> None:
    if not project_id:
        return
    if not _PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ValueError(
            "platform.project_id must be the 32-character project_id returned by "
            "GET /api/projects, not the project display name"
        )


def _validate_runtime_memory_target(
    runtime_params: dict[str, Any],
    memory_proxy: MemoryProxyConfig,
) -> None:
    if not memory_proxy.enabled:
        return
    memory = runtime_params.get("memory")
    if not isinstance(memory, dict):
        raise ValueError(
            "rollout.runtime_params.memory must be configured when memory_proxy is enabled"
        )
    if memory.get("enabled") is not True:
        raise ValueError(
            "rollout.runtime_params.memory.enabled must be true when memory_proxy is enabled"
        )
    runtime_target = _optional_text(memory.get("openviking_target"))
    if runtime_target != memory_proxy.openviking_target:
        raise ValueError(
            "rollout.runtime_params.memory.openviking_target must equal "
            "memory_proxy.openviking_target"
        )


def _validate_task_memory_target(
    agent_execution: dict[str, Any],
    memory_proxy: MemoryProxyConfig,
) -> None:
    if not memory_proxy.enabled:
        return
    values = agent_execution.get("values")
    if not isinstance(values, dict):
        raise ValueError(
            "training_task.agent_execution.values must be configured when memory_proxy is enabled"
        )
    task_target = _optional_text(values.get("memory_openviking_target"))
    if task_target != memory_proxy.openviking_target:
        raise ValueError(
            "training_task.agent_execution.values.memory_openviking_target must equal "
            "memory_proxy.openviking_target"
        )


def _relative_path(value: str, *, base: Path) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def _read_json_text(path: Path, dotted_path: str) -> str:
    try:
        value: Any = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"memory proxy config file does not exist: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read memory proxy config file {path}: {exc}") from exc
    for segment in dotted_path.split("."):
        if not isinstance(value, dict) or segment not in value:
            raise ValueError(f"memory proxy config file {path} has no JSON path {dotted_path!r}")
        value = value[segment]
    result = _optional_text(value)
    if not result:
        raise ValueError(f"memory proxy config file {path} has an empty JSON path {dotted_path!r}")
    return result


def main() -> None:
    args = parse_args()
    try:
        asyncio.run(run(load_config(args.config)))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
