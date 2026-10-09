"""Single-file configuration for the direct Viking adapter."""

import json
from pathlib import Path
from typing import Annotated, Any

from memory_proxy import MemoryProxyConfig
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class SettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Selection(SettingsModel):
    experiment_set_id: int = Field(gt=0)
    version: str = Field(min_length=1)
    row_ids: list[int] = Field(default_factory=list)


class Service(SettingsModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8765, gt=0, le=65535)
    dataset: str = "ark4-0"
    domain: str = "ark"
    log_level: str = "info"
    state_dir: str = ".adapter-state"
    max_active_tasks: int = Field(default=1, ge=1, le=2)


class Viking(SettingsModel):
    base_url: str = "https://viking-exp.byted.org"
    api_token: str = Field(repr=False)
    template_task_id: int = Field(gt=0)
    group_concurrency: dict[str, Annotated[int, Field(ge=1, le=500)]] = Field(default_factory=dict)
    group_timeout_seconds: dict[str, Annotated[int, Field(ge=1, le=604800)]] = Field(
        default_factory=dict
    )
    train: Selection | None = None
    eval: Selection | None = None
    sandbox_config: dict[str, Any] = Field(default_factory=dict)
    score_column: str = "answer_score"
    pass_threshold: float = Field(default=1.0, ge=0, le=1)
    poll_interval_seconds: float = Field(default=3.0, gt=0)
    task_time_limit_seconds: int = Field(default=14400, ge=60, le=604800)


class AdapterSettings(SettingsModel):
    service: Service = Field(default_factory=Service)
    viking: Viking
    memory_proxy: dict[str, Any] = Field(default_factory=dict)
    kubevpn: dict[str, Any] = Field(default_factory=dict)


def load_settings(path):
    path = Path(path).expanduser().resolve()
    try:
        settings = AdapterSettings.model_validate_json(path.read_text())
    except ValidationError as exc:
        details = [".".join(map(str, e["loc"])) + ": " + e["msg"] for e in exc.errors()]
        raise ValueError("Invalid adapter configuration: " + "; ".join(details)) from None
    if not settings.viking.api_token.strip():
        raise ValueError(
            "请在 adapter_config.local.json 的 viking.api_token 填入 Viking API Token（不是旧网关 API Key）"
        )
    if not settings.viking.base_url.startswith("https://"):
        raise ValueError("viking.base_url must use HTTPS")
    return settings


def proxy_config(settings, config_path):
    raw = dict(settings.memory_proxy)
    filename = raw.pop("openviking_config_file", None)
    key_path = raw.pop("openviking_api_key_json_path", "server.root_api_key")
    base = Path(config_path).resolve().parent
    if filename:
        value = json.loads((base / Path(filename).expanduser()).read_text())
        for component in key_path.split("."):
            value = value[component]
        raw["openviking_api_key"] = str(value)
    if raw.get("event_log_file"):
        raw["event_log_file"] = base / Path(raw["event_log_file"]).expanduser()
    memory = (
        settings.viking.sandbox_config.get("extra_payload", {})
        .get("extra_data", {})
        .get("extra", {})
        .get("memory", {})
    )
    if memory.get("enabled") and raw.get("enabled"):
        if memory.get("openviking_target") != raw.get("openviking_target"):
            raise ValueError(
                "sandbox memory.openviking_target must match memory_proxy.openviking_target"
            )
    return MemoryProxyConfig(**raw)
