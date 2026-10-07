"""Non-interactive OpenViking configuration adapter for Hermes Desktop."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Mapping, Optional

from agent.memory_provider import MemoryProviderConfigConflictError


class OpenVikingProfileNotFoundError(ValueError):
    """Hermes is linked to an OpenViking profile that no longer exists."""


def _ov():
    """Resolve the plugin lazily so tests and runtime reloads share one state."""
    return sys.modules[__package__]


def _profile_description(profile) -> str:
    ov = _ov()
    endpoint = ov._clean_config_value(profile.values.get("endpoint")) or ov._DEFAULT_ENDPOINT
    return f"{endpoint} ({profile.path})"


def _profile_payload(profile, *, active_path: Optional[Path]) -> dict:
    ov = _ov()
    is_active = bool(
        active_path and ov._profile_identity(profile.path) == ov._profile_identity(active_path)
    )
    label = ov._setup._profile_display_name(profile)
    return {
        "value": str(profile.path),
        "label": f"{label} (Active)" if is_active else label,
        "description": _profile_description(profile),
    }


def _profiles(
    active_path: Optional[Path],
    *,
    env_values: Mapping[str, str],
) -> list:
    ov = _ov()
    profiles = ov._discover_ovcli_profiles(env=env_values)
    if active_path and active_path.is_file():
        active_identity = ov._profile_identity(active_path)
        if not any(ov._profile_identity(profile.path) == active_identity for profile in profiles):
            profile = ov._load_profile(active_path, source="active", name="active")
            if profile is not None:
                profiles.append(profile)
    return profiles


def _connection_type(endpoint: str) -> str:
    ov = _ov()
    return (
        "OpenViking Service"
        if endpoint.rstrip("/") == ov._OPENVIKING_SERVICE_ENDPOINT.rstrip("/")
        else "Custom"
    )


def _health(state: str, label: str, message: str) -> dict:
    return {"state": state, "label": label, "message": message}


def _health_for_settings(settings: dict) -> dict:
    ov = _ov()
    ok, message, _role = ov._validate_openviking_setup_values(
        settings,
        require_api_key=not ov._is_local_openviking_url(settings.get("endpoint", "")),
    )
    if ok:
        return _health("healthy", "Healthy", "OpenViking is ready to use.")

    lowered = (message or "").lower()
    unreachable = any(
        marker in lowered
        for marker in (
            "not reachable",
            "connection refused",
            "connection reset",
            "timed out",
            "timeout",
        )
    )
    return _health(
        "unreachable" if unreachable else "unhealthy",
        "Unreachable" if unreachable else "Unhealthy",
        message,
    )


def snapshot(*, hermes_home: str | Path, probe_health: bool) -> dict:
    """Return a redacted profile-scoped snapshot for the shared settings form."""
    ov = _ov()
    provider_config = ov._load_hermes_openviking_config(str(hermes_home))
    env_values = ov._profile_openviking_env(str(hermes_home)) or {}
    active_path: Optional[Path] = None
    missing_profile = False
    invalid_profile = False

    if provider_config.get("use_ovcli_config"):
        active_path = ov._provider_ovcli_config_path(
            provider_config, env=env_values, hermes_home=hermes_home
        )
        missing_profile = not active_path.is_file()

    if missing_profile:
        settings = {
            "endpoint": ov._DEFAULT_ENDPOINT,
            "api_key": "",
            "account": "",
            "user": "",
            "agent": ov._DEFAULT_AGENT,
        }
    else:
        try:
            settings = ov._resolve_connection_settings(
                provider_config,
                env=env_values,
                hermes_home=hermes_home,
            )
        except (OSError, UnicodeError, ValueError):
            if not provider_config.get("use_ovcli_config"):
                raise
            invalid_profile = True
            settings = {
                "endpoint": ov._DEFAULT_ENDPOINT,
                "api_key": "",
                "account": "",
                "user": "",
                "agent": ov._DEFAULT_AGENT,
            }

    profiles = _profiles(active_path, env_values=env_values)
    active_profile = next(
        (
            profile
            for profile in profiles
            if active_path
            and ov._profile_identity(profile.path) == ov._profile_identity(active_path)
        ),
        None,
    )
    source_type = _connection_type(settings.get("endpoint") or ov._DEFAULT_ENDPOINT)
    if active_profile is not None:
        active_label = f"{ov._setup._profile_display_name(active_profile)} ({source_type})"
    elif provider_config.get("use_ovcli_config"):
        active_label = "Missing profile"
    else:
        active_label = f"Environment variables ({source_type})"

    if missing_profile:
        health = _health(
            "unreachable",
            "Profile missing",
            "The linked OpenViking profile no longer exists. Choose another profile or recreate it.",
        )
    elif invalid_profile:
        health = _health(
            "unreachable",
            "Profile invalid",
            "The linked OpenViking profile could not be read. Fix it or choose another profile.",
        )
    elif probe_health:
        health = _health_for_settings(settings)
    else:
        health = _health(
            "checking",
            "Checking",
            "Checking OpenViking connection status.",
        )

    current_path = str(active_path) if active_path and active_path.is_file() else ""
    setup_type = (
        "profile"
        if current_path
        else "service"
        if source_type == "OpenViking Service"
        else "custom"
    )
    if provider_config.get("deployment") == ov.quick_local.DEPLOYMENT:
        setup_type = "quick_local"
        if not missing_profile and not invalid_profile:
            active_label = "Quick Local"
    credential = "none"
    if settings.get("api_key"):
        credential = (
            "root" if active_profile and active_profile.values.get("root_api_key") else "user"
        )

    return {
        "values": {
            "setup_type": setup_type,
            "usage_profile": "shared"
            if provider_config.get("recall_scope") == "shared"
            else "personal",
            "profile_path": current_path,
            "profile_name": "openviking",
            "url": settings.get("endpoint") or ov._DEFAULT_ENDPOINT,
            "credential": credential,
            "api_key_service": "",
            "api_key": "",
            "account": settings.get("account") or "",
            "user": settings.get("user") or "",
            "actor_peer_id": settings.get("agent") or ov._DEFAULT_AGENT,
        },
        "options": {
            "profile_path": [
                _profile_payload(profile, active_path=active_path) for profile in profiles
            ]
        },
        "summary": {
            "items": [
                {"label": "Active profile", "value": active_label},
                {
                    "label": "OpenViking URL",
                    "value": settings.get("endpoint") or ov._DEFAULT_ENDPOINT,
                },
            ],
            "status": health,
        },
    }


def connection_values(values: dict) -> tuple[dict, str]:
    """Normalize submitted form values and discard fields hidden by its mode."""
    ov = _ov()
    setup_type = ov._clean_config_value(values.get("setup_type"))
    if setup_type not in {"service", "custom"}:
        raise ValueError("Choose OpenViking Service, Existing Profiles, or Custom Server.")

    endpoint = (
        ov._OPENVIKING_SERVICE_ENDPOINT
        if setup_type == "service"
        else ov._clean_config_value(values.get("url"))
    )
    credential = (
        "user" if setup_type == "service" else ov._clean_config_value(values.get("credential"))
    )
    if credential not in {"none", "user", "root"}:
        raise ValueError("Choose No API key, User API key, or Root API key.")
    if credential == "none" and not ov._is_local_openviking_url(endpoint):
        raise ValueError("Remote OpenViking servers require an API key.")

    submitted_key = ov._clean_config_value(
        values.get("api_key_service") if setup_type == "service" else values.get("api_key")
    )
    api_key = "" if credential == "none" else submitted_key
    if credential != "none" and not api_key:
        raise ValueError("Enter an OpenViking API key.")

    account = ov._clean_config_value(values.get("account")) if credential == "root" else ""
    user = ov._clean_config_value(values.get("user")) if credential == "root" else ""
    if credential == "root" and (not account or not user):
        raise ValueError("Account and User are required for a Root API key.")

    for field, value in (("account", account), ("user", user)):
        if value:
            valid, message, _normalized = ov._validate_openviking_identity_value(value, field=field)
            if not valid:
                raise ValueError(message)
    actor_peer_id = ov._clean_config_value(values.get("actor_peer_id"))
    if "/" in actor_peer_id or "\\" in actor_peer_id:
        raise ValueError("Agent ID cannot contain '/' or '\\'.")

    return {
        "endpoint": ov._normalize_openviking_url(endpoint),
        "api_key": api_key,
        "root_api_key": api_key if credential == "root" else "",
        "account": account,
        "user": user,
        "agent": actor_peer_id,
        "api_key_type": credential,
    }, credential


def validate_values(values: dict) -> tuple[dict, Optional[str]]:
    ov = _ov()
    connection, credential = connection_values(values)
    ok, message, role = ov._validate_openviking_setup_values(
        connection,
        require_api_key=not ov._is_local_openviking_url(connection["endpoint"]),
    )
    if not ok:
        raise ValueError(message)
    if credential == "user" and role == "root":
        raise ValueError(
            "This key has root access. Select Root API key and provide Account and User, "
            "or enter a User API key."
        )
    if credential == "root" and role == "user":
        raise ValueError("This is a User API key. Select User API key, or enter a Root API key.")
    return connection, role


def save(*, values: dict, hermes_home: str | Path, overwrite: bool, confirmations: dict) -> dict:
    """Validate and provision before activating the profile; never mutate process env."""
    from hermes_cli.config import _CONFIG_LOCK, load_config, save_config
    from plugins.memory.desktop_setup import report_progress

    from . import quick_local

    ov = _ov()
    home = Path(hermes_home)
    usage = values.get("usage_profile", "personal")
    if usage not in {"personal", "shared"}:
        raise ValueError("Choose Personal Agent or Shared Agent.")
    if usage == "shared" and confirmations.get("shared") is not True:
        raise MemoryProviderConfigConflictError(
            "Shared Agent shares conversation history within each group or thread and recalls all senders under this OpenViking user. Apply this change and restart the Hermes gateway?",
            confirmation="shared",
        )

    setup_type = ov._clean_config_value(values.get("setup_type"))
    report_progress("validate", "Validating the selected OpenViking connection...")
    if setup_type == "quick_local":
        setup = quick_local.QuickLocalSetup(
            health_check=ov._validate_openviking_reachability,
            progress=lambda event: report_progress(event.stage.value, event.message),
            allow_source_build=confirmations.get("source_build") is True,
        )
        try:
            result = setup.provision(hermes_home=home, preflight=setup.preflight(home))
        except quick_local.SourceBuildRequired as exc:
            raise MemoryProviderConfigConflictError(str(exc), confirmation="source_build") from exc
        except quick_local.QuickLocalSetupError as exc:
            raise ValueError(str(exc)) from exc
        path = result.paths.ovcli_config
    elif setup_type == "profile":
        path = Path(ov._clean_config_value(values.get("profile_path"))).expanduser()
        if not path.is_file():
            raise OpenVikingProfileNotFoundError(
                "The selected OpenViking profile no longer exists. Refresh profiles and retry."
            )
        profile = ov._load_profile(path, source="saved", name=path.name)
        if profile is None:
            raise ValueError("The selected OpenViking profile could not be read.")
        ok, message, _role = ov._validate_openviking_setup_values(
            profile.values,
            require_api_key=not ov._is_local_openviking_url(profile.values.get("endpoint", "")),
        )
        if not ok:
            raise ValueError(message)
    else:
        connection, _role = validate_values(values)
        profile_name = ov._clean_config_value(values.get("profile_name"))
        if not ov._is_valid_ovcli_profile_name(profile_name) or profile_name == "active":
            raise ValueError(
                "Use a profile name with letters, numbers, '-' or '_'. The name 'active' is reserved."
            )
        path = ov._default_ovcli_config_path().parent / f"{ov._OVCLI_SAVED_PREFIX}{profile_name}"
        new_data = ov._ovcli_data_from_connection_values(connection)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Publish a new profile without replacing one concurrently created by
        # another setup. Overwrite remains an explicit, atomic replacement.
        with tempfile.TemporaryDirectory(prefix=".ov-setup-", dir=path.parent) as temporary:
            staged = Path(temporary) / "profile.json"
            ov.atomic_json_write(staged, new_data, mode=0o600)
            try:
                os.link(staged, path)
            except FileExistsError:
                try:
                    existing_data = ov._load_ovcli_config(path)
                except (OSError, UnicodeError, ValueError):
                    existing_data = None
                if existing_data != new_data:
                    if not overwrite:
                        raise MemoryProviderConfigConflictError(
                            "An OpenViking profile with this name has different settings. Replace it?"
                        )
                    ov.atomic_json_write(path, new_data, mode=0o600)

    report_progress("save", "Saving this Hermes profile's OpenViking settings...")
    # Re-read after network/install work. The shared CLI helper owns connection-key
    # cleanup; global environment mutation is explicitly disabled for Desktop.
    previous = ov._load_hermes_openviking_config(str(home))
    with _CONFIG_LOCK:
        config = load_config()
        memory = config.setdefault("memory", {})
        provider_config = dict(memory.get("openviking") or {})
        before = dict(provider_config)
        ov._setup._link_ovcli_profile(
            config=config,
            provider_config=provider_config,
            env_path=home / ".env",
            ovcli_path=path,
            update_process_env=False,
            stop_previous=False,
        )
        if setup_type == "quick_local":
            provider_config["deployment"] = quick_local.DEPLOYMENT
        ov._setup._apply_usage_profile(config, provider_config, usage)
        ov._write_env_vars(home / ".env", {}, remove_keys=("OPENVIKING_RECALL_SCOPE",))
        changed = {
            key: provider_config.get(key)
            for key in before.keys() | provider_config.keys()
            if before.get(key) != provider_config.get(key)
        }
        patch = {"memory": {"provider": "openviking", "openviking": changed}}
        if usage == "shared":
            patch.update(group_sessions_per_user=False, thread_sessions_per_user=False)
        save_config(patch, merge_existing=True)
    ov._setup._stop_previous_quick_local(previous, home, ovcli_path=path)
    remaining_env = ov._profile_openviking_env(str(home)) or {}
    restart_required = setup_type != "quick_local" and any(
        remaining_env.get(key)
        for key in (*ov._OPENVIKING_ENV_KEYS, ov._OVCLI_CONFIG_ENV, "OPENVIKING_RECALL_SCOPE")
    )
    message = (
        "OpenViking setup saved. Restart this Hermes connection to clear its previous environment settings."
        if restart_required
        else "OpenViking setup saved. Start a new chat to use it."
    )
    if usage == "shared":
        message += " Restart the Hermes gateway to apply shared conversation history."
    return {"ok": True, "profile_path": str(path), "message": message}
