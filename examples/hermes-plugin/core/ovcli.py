"""OpenViking CLI (``ovcli.conf``) profiles: paths, parsing and discovery."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

from .endpoint import _normalize_openviking_url
from .http import _format_openviking_exception
from .log import get_logger
from .settings import _DEFAULT_ENDPOINT, _clean_config_value

logger = get_logger()


_OVCLI_CONFIG_ENV = "OPENVIKING_CLI_CONFIG_FILE"
_OVCLI_DEFAULT_RELATIVE_PATH = ".openviking/ovcli.conf"
_OVCLI_SAVED_PREFIX = "ovcli.conf."


@dataclass(frozen=True)
class _OvcliProfile:
    source: str
    name: str
    path: Path
    values: dict
    is_active: bool = False


def _default_ovcli_config_path() -> Path:
    return Path.home() / _OVCLI_DEFAULT_RELATIVE_PATH


def _resolve_ovcli_config_path(config_path: str = "", *, env: Optional[dict] = None) -> Path:
    chosen = (os.environ if env is None else env).get(_OVCLI_CONFIG_ENV, "").strip() or config_path
    return Path(chosen).expanduser() if chosen else _default_ovcli_config_path()


def _load_ovcli_config(path: Optional[Path] = None) -> dict:
    config_path = path or _resolve_ovcli_config_path()
    if not config_path.exists():
        return {}
    data = json.loads(config_path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"OpenViking CLI config must be a JSON object: {config_path}")
    return data


def _connection_values_from_ovcli(data: dict) -> dict:
    endpoint_value = _clean_config_value(data.get("url"))
    api_key = _clean_config_value(data.get("api_key")) or _clean_config_value(data.get("root_api_key"))
    root_api_key = _clean_config_value(data.get("root_api_key"))
    send_identity = not api_key or api_key == root_api_key  # user keys derive tenant server-side
    return {
        # No URL -> no endpoint; the resolver continues to config.yaml, then the default.
        "endpoint": _normalize_openviking_url(endpoint_value) if endpoint_value else "",
        "api_key": api_key,
        "root_api_key": root_api_key,
        "account": _clean_config_value(data.get("account") or data.get("account_id")) if send_identity else "",
        "user": _clean_config_value(data.get("user") or data.get("user_id")) if send_identity else "",
        "agent": _clean_config_value(data.get("actor_peer_id") or data.get("agent_id")),
    }


def _is_valid_ovcli_profile_name(name: str) -> bool:
    if not name or name.strip() != name or name.startswith(".") or "/" in name or "\\" in name:
        return False
    return all(ch.isascii() and (ch.isalnum() or ch in {"-", "_"}) for ch in name)


def _load_profile(path: Path, *, source: str, name: str) -> Optional[_OvcliProfile]:
    try:
        values = _connection_values_from_ovcli(_load_ovcli_config(path))
    except Exception as e:
        logger.warning("Skipping invalid OpenViking CLI config %s: %s", path, _format_openviking_exception(e))
        return None
    return _OvcliProfile(source=source, name=name, path=path, values=values)


def _profile_identity(path: Path) -> str:
    try:
        return str(path.expanduser().resolve())
    except OSError:
        return str(path.expanduser())


def _discover_ovcli_profiles() -> list[_OvcliProfile]:
    """env-pointed config, then saved ``ovcli.conf.<name>`` files, then the active
    ``ovcli.conf`` — which is only listed on its own when no saved profile has
    identical connection values and nothing else was found."""
    profiles: list[_OvcliProfile] = []
    seen_paths: set[str] = set()

    def add(path: Path, *, source: str, name: str) -> None:
        identity = _profile_identity(path)
        if path.is_file() and identity not in seen_paths and (profile := _load_profile(path, source=source, name=name)) is not None:
            seen_paths.add(identity)
            profiles.append(profile)

    env_path = os.environ.get(_OVCLI_CONFIG_ENV, "").strip()
    if env_path:
        add(Path(env_path).expanduser(), source="env", name=_OVCLI_CONFIG_ENV)

    active_path = _default_ovcli_config_path()
    active_profile = _load_profile(active_path, source="active", name="active") if active_path.exists() else None

    config_dir = _default_ovcli_config_path().parent
    saved_start = len(profiles)
    if config_dir.exists():
        for path in sorted(config_dir.iterdir(), key=lambda item: item.name):
            name = path.name.removeprefix(_OVCLI_SAVED_PREFIX)
            if path.is_file() and name != path.name and name != "bak" and _is_valid_ovcli_profile_name(name):
                add(path, source="saved", name=name)

    if active_profile is not None:
        marked_active = False
        for idx in range(saved_start, len(profiles)):
            if profiles[idx].source == "saved" and profiles[idx].values == active_profile.values:
                profiles[idx] = replace(profiles[idx], is_active=True)
                marked_active = True
                break
        if not marked_active and not profiles and _profile_identity(active_profile.path) not in seen_paths:
            profiles.append(active_profile)
    return profiles


def _ovcli_values_for(provider_config: dict, *, env: Optional[dict] = None) -> dict:
    """Connection values from the linked ovcli profile, or {} when none is linked."""
    if not provider_config.get("use_ovcli_config"):
        return {}
    ovcli_path = _resolve_ovcli_config_path(str(provider_config.get("ovcli_config_path") or ""), env=env)
    return _connection_values_from_ovcli(_load_ovcli_config(ovcli_path))


def _ovcli_data_from_connection_values(values: dict) -> dict:
    data = {"url": _normalize_openviking_url(_clean_config_value(values.get("endpoint")) or _DEFAULT_ENDPOINT)}
    for out_key, in_key in (("api_key", "api_key"), ("root_api_key", "root_api_key"), ("account", "account"), ("user", "user"), ("actor_peer_id", "agent")):
        value = _clean_config_value(values.get(in_key))
        if value:
            data[out_key] = value
    return data
