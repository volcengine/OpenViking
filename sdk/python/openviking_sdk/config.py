from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

OPENVIKING_CLI_CONFIG_ENV = "OPENVIKING_CLI_CONFIG_FILE"
DEFAULT_OVCLI_CONF = Path.home() / ".openviking" / "ovcli.conf"


@dataclass(frozen=True)
class ClientConfig:
    url: str
    api_key: Optional[str]
    account: Optional[str]
    user: Optional[str]
    actor_peer_id: Optional[str]
    timeout: float
    profile_enabled: bool
    extra_headers: dict[str, str]
    upload_mode: Optional[str]


@dataclass(frozen=True)
class OVCLIConfig:
    url: Optional[str]
    api_key: Optional[str]
    account: Optional[str]
    user: Optional[str]
    actor_peer_id: Optional[str]
    agent_id: Optional[str]
    timeout: float
    profile: bool
    extra_headers: dict[str, str]
    upload_mode: Optional[str]
    output: Optional[str]


def _resolve_ovcli_config_path() -> Optional[Path]:
    config_path = os.getenv(OPENVIKING_CLI_CONFIG_ENV)
    if config_path:
        return Path(config_path).expanduser()
    if DEFAULT_OVCLI_CONF.exists():
        return DEFAULT_OVCLI_CONF
    return None


def _require_mapping(value: object, *, path: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"Invalid value for '{path}': expected object")
    return value


def _optional_string(value: object, *, path: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Invalid value for '{path}': expected string")
    return value


def _optional_bool(value: object, *, path: str) -> Optional[bool]:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"Invalid value for '{path}': expected boolean")
    return value


def _optional_float(value: object, *, path: str) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"Invalid value for '{path}': expected number")
    if not isinstance(value, (int, float)):
        raise ValueError(f"Invalid value for '{path}': expected number")
    return float(value)


def _parse_extra_headers(value: object, *, path: str) -> dict[str, str]:
    if value is None:
        return {}
    data = _require_mapping(value, path=path)
    parsed: dict[str, str] = {}
    for key, header_value in data.items():
        if not isinstance(key, str):
            raise ValueError(f"Invalid value for '{path}': expected string keys")
        if not isinstance(header_value, str):
            raise ValueError(f"Invalid value for '{path}.{key}': expected string")
        parsed[key] = header_value
    return parsed


def load_ovcli_config(config_path: Optional[str] = None) -> Optional[OVCLIConfig]:
    path = Path(config_path).expanduser() if config_path else _resolve_ovcli_config_path()
    if path is None or not path.exists():
        return None

    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid CLI config in {path}: {exc.msg}") from exc

    try:
        data = _require_mapping(raw, path="ovcli")

        allowed_keys = {
            "url",
            "api_key",
            "account",
            "user",
            "actor_peer_id",
            "agent_id",
            "timeout",
            "profile",
            "upload",
            "extra_headers",
            "extra_header",
            "output",
            "root_api_key",
        }
        unknown_keys = sorted(set(data) - allowed_keys)
        if unknown_keys:
            raise ValueError(f"Unknown field 'ovcli.{unknown_keys[0]}'")

        extra_header_alias = data.get("extra_header")
        extra_headers_value = data.get("extra_headers", extra_header_alias)

        upload_data = data.get("upload")
        upload_mode = None
        if upload_data is not None:
            upload = _require_mapping(upload_data, path="ovcli.upload")
            allowed_upload_keys = {"mode", "ignore_dirs", "include", "exclude"}
            unknown_upload_keys = sorted(set(upload) - allowed_upload_keys)
            if unknown_upload_keys:
                raise ValueError(f"Unknown field 'ovcli.upload.{unknown_upload_keys[0]}'")
            upload_mode = _optional_string(upload.get("mode"), path="ovcli.upload.mode")

        actor_peer_id = _optional_string(data.get("actor_peer_id"), path="ovcli.actor_peer_id")
        agent_id = _optional_string(data.get("agent_id"), path="ovcli.agent_id")
        if actor_peer_id is not None and agent_id is not None:
            raise ValueError("actor_peer_id cannot be used with agent_id")

        timeout = _optional_float(data.get("timeout"), path="ovcli.timeout")
        profile = _optional_bool(data.get("profile"), path="ovcli.profile")

        return OVCLIConfig(
            url=_optional_string(data.get("url"), path="ovcli.url"),
            api_key=_optional_string(data.get("api_key"), path="ovcli.api_key"),
            account=_optional_string(data.get("account"), path="ovcli.account"),
            user=_optional_string(data.get("user"), path="ovcli.user"),
            actor_peer_id=actor_peer_id,
            agent_id=agent_id,
            timeout=60.0 if timeout is None else timeout,
            profile=False if profile is None else profile,
            extra_headers=_parse_extra_headers(extra_headers_value, path="ovcli.extra_headers"),
            upload_mode=upload_mode,
            output=_optional_string(data.get("output"), path="ovcli.output"),
        )
    except ValueError as exc:
        raise ValueError(f"Invalid CLI config in {path}: {exc}") from exc


def resolve_client_config(
    *,
    url: Optional[str] = None,
    api_key: Optional[str] = None,
    account: Optional[str] = None,
    user: Optional[str] = None,
    actor_peer_id: Optional[str] = None,
    timeout: float = 60.0,
    extra_headers: Optional[dict[str, str]] = None,
    profile_enabled: Optional[bool] = None,
    upload_mode: Optional[str] = None,
) -> ClientConfig:
    cli_config = load_ovcli_config()

    resolved_url = url or os.getenv("OPENVIKING_URL") or (cli_config.url if cli_config else None)
    resolved_api_key = (
        api_key or os.getenv("OPENVIKING_API_KEY") or (cli_config.api_key if cli_config else None)
    )
    resolved_account = (
        account or os.getenv("OPENVIKING_ACCOUNT") or (cli_config.account if cli_config else None)
    )
    resolved_user = (
        user or os.getenv("OPENVIKING_USER") or (cli_config.user if cli_config else None)
    )
    resolved_actor_peer_id = (
        actor_peer_id
        or os.getenv("OPENVIKING_ACTOR_PEER_ID")
        or (cli_config.actor_peer_id if cli_config else None)
    )
    if resolved_actor_peer_id is None and cli_config is not None and cli_config.agent_id:
        resolved_actor_peer_id = cli_config.agent_id

    resolved_timeout = timeout
    if timeout == 60.0:
        env_timeout = os.getenv("OPENVIKING_TIMEOUT")
        if env_timeout:
            resolved_timeout = float(env_timeout)
        elif cli_config is not None:
            resolved_timeout = cli_config.timeout

    resolved_profile_enabled = bool(profile_enabled)
    if profile_enabled is None and cli_config is not None:
        resolved_profile_enabled = cli_config.profile

    resolved_extra_headers = dict(extra_headers) if extra_headers is not None else {}
    if extra_headers is None and cli_config is not None:
        resolved_extra_headers = dict(cli_config.extra_headers)

    resolved_upload_mode = upload_mode
    if resolved_upload_mode is None and cli_config is not None:
        resolved_upload_mode = cli_config.upload_mode

    if not resolved_url:
        raise ValueError(
            "url is required. Pass it explicitly, set OPENVIKING_URL, or configure ovcli.conf."
        )

    return ClientConfig(
        url=resolved_url.rstrip("/"),
        api_key=resolved_api_key,
        account=resolved_account,
        user=resolved_user,
        actor_peer_id=resolved_actor_peer_id,
        timeout=resolved_timeout,
        profile_enabled=resolved_profile_enabled,
        extra_headers=resolved_extra_headers,
        upload_mode=resolved_upload_mode,
    )
