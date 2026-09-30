"""Config schema, connection defaults and typed, range-clamped settings."""

from __future__ import annotations

import math
import threading
from typing import Any, Optional, Set

from .host import get_secret
from .log import get_logger

logger = get_logger()


_DEFAULT_ENDPOINT = "http://127.0.0.1:1933"
_OPENVIKING_SERVICE_ENDPOINT = "https://api.vikingdb.cn-beijing.volces.com/openviking"
_DEFAULT_AGENT = ""
_CONNECTION_KEYS = ("endpoint", "api_key", "account", "user", "agent")
_OPENVIKING_ENV_KEYS = tuple(f"OPENVIKING_{key.upper()}" for key in _CONNECTION_KEYS)
_DEFAULT_RECALL_REQUEST_TIMEOUT_SECONDS = 3.0


def _cfg_field(key: str, description: str, **extra) -> dict:
    return {"key": key, "description": description, **extra, "env_var": f"OPENVIKING_{key.upper()}"}


_NUM = {"type": "number", "minimum": 0.25, "maximum": 60.0, "step": 0.25}
_CONFIG_SCHEMA = [
    _cfg_field("endpoint", "OpenViking server URL", required=True, default=_DEFAULT_ENDPOINT),
    _cfg_field("api_key", (
        "OpenViking API key (recommended; only leave blank for an explicitly "
        "unauthenticated local development server)"
    ), secret=True),
    _cfg_field("account", "Advanced local identity override (leave blank for user API keys)"),
    _cfg_field("user", "Advanced local user override (leave blank for user API keys)"),
    _cfg_field("agent", "Optional peer ID for separate assistant context. Uses user memory when no peer is configured.", default=_DEFAULT_AGENT),
    _cfg_field("recall_scope", "Automatic recall: all peers (shared) or current sender (peer). Unset preserves existing behavior.",
               type="string", choices=["shared", "peer"], default=None),
    _cfg_field(
        "recall_compress",
        "Cloud recall compression: off, server or auto (no local compressor)",
        type="string",
        default="off",
    ),
    _cfg_field("commit_token_threshold", "Pending session tokens that trigger a background memory commit",
               type="integer", minimum=1000, maximum=1000000, default=20000),
    _cfg_field("recall_limit", "Maximum memories injected by automatic recall", type="integer", minimum=1, maximum=100, default=6),
    _cfg_field("recall_score_threshold", "Minimum relevance score for automatic recall", type="number", minimum=0.0, maximum=1.0, step=0.01, default=0.15),
    _cfg_field("recall_max_injected_chars", "Maximum total characters injected by recall", type="integer", minimum=100, maximum=50000, default=4000),
    _cfg_field("profile_token_budget", "Maximum session-start memory tokens injected", type="integer", minimum=500, maximum=50000, default=6000),
    _cfg_field("recall_timeout_seconds", "Total timeout for recall (seconds)", **_NUM, default=4.0),
    _cfg_field("recall_request_timeout_seconds", "Per-request timeout for recall (seconds)", **_NUM, default=_DEFAULT_RECALL_REQUEST_TIMEOUT_SECONDS),
    _cfg_field("recall_full_read_limit", "Max full L2 content reads per recall", type="integer", minimum=0, maximum=100, default=2),
    _cfg_field("recall_prefer_abstract", "Use abstracts instead of full L2 reads", type="boolean", default=False),
    _cfg_field("recall_resources", "Include resources in recall", type="boolean", default=False),
    _cfg_field("recall_context_mode", "Use server context mode for shared and sender-scoped recall (false: list search)",
               type="boolean", default=True),
    _cfg_field("extra_tools", "Optional OpenViking tools to expose besides the defaults, comma-separated "
               "(write, edit, add_skill, list_watches, cancel_watch)", default=""),
]
# Typed settings (config.yaml primary, env override) keyed by config key.
_SETTING_SPECS = {f["key"]: f for f in _CONFIG_SCHEMA if "type" in f}
_RECALL_SETTING_KEYS = tuple(k for k in _SETTING_SPECS if k.startswith("recall_"))
_INVALID_SETTING_WARNINGS: Set[tuple[str, str]] = set()
_INVALID_SETTING_WARNINGS_LOCK = threading.Lock()


def _clean_config_value(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _validate_openviking_identity_value(value: str, *, field: str) -> tuple[bool, str, str]:
    label = "Account ID" if field == "account" else "User ID"
    identifier = "account_id" if field == "account" else "user_id"
    trimmed = value.strip()
    if not trimmed:
        return False, f"{label} cannot be empty.", ""
    if trimmed != value:
        return False, f"{label} cannot start or end with whitespace.", ""
    if field == "account" and trimmed.startswith("_"):
        return False, "Account ID cannot start with '_'.", ""
    if not all(ch.isascii() and (ch.isalnum() or ch in {"_", "-", ".", "@"}) for ch in trimmed):
        return False, f"{label} can only contain letters, numbers, '_', '-', '.', and '@'.", ""
    if trimmed.count("@") > 1:
        return False, f"{identifier} must have at most one '@'.", ""
    return True, "", trimmed


class SettingsMixin:
    """Methods of ``OpenVikingMemoryProvider`` moved here unchanged; mixed into that class."""

    @staticmethod
    def _parse_setting_value(value: Any, kind: str) -> Optional[bool | int | float | str]:
        """Parse per schema ``kind`` (boolean / integer / number); None when invalid."""
        if kind == "string":
            normalized = str(value).strip().lower()
            if normalized in {"1", "true", "yes"}:
                return "auto"
            if normalized in {"0", "false", "no"}:
                return "off"
            return normalized if normalized in {"off", "server", "auto"} else None
        if kind == "boolean":
            if isinstance(value, bool):
                return value
            normalized = value.strip().lower() if isinstance(value, str) else None
            return True if normalized in {"1", "true", "yes", "on"} else False if normalized in {"0", "false", "no", "off"} else None
        try:
            if isinstance(value, bool):
                return None
            numeric = float(value)
            if not math.isfinite(numeric) or (kind == "integer" and not numeric.is_integer()):
                return None
            return int(numeric) if kind == "integer" else numeric
        except (TypeError, ValueError, OverflowError):
            return None

    @classmethod
    def _setting(cls, key: str, provider_config: dict, *, env: Optional[dict] = None) -> Any:
        """Typed, range-clamped setting per _SETTING_SPECS (config.yaml primary, env override);
        an invalid value falls back to the default with one warning per (source, value)."""
        spec = _SETTING_SPECS[key]
        default = spec["default"]
        env_value = get_secret(spec["env_var"]) if env is None else env.get(spec["env_var"])
        if env_value is not None and env_value.strip():
            value, source = env_value, spec["env_var"]
        else:
            value, source = provider_config.get(key, default), f"memory.openviking.{key}"
        if value is None and default is None:
            return None  # No policy override on installations that predate the presets.
        parsed = str(value).strip().lower() if "choices" in spec else cls._parse_setting_value(value, spec["type"])
        if "choices" in spec and parsed not in spec["choices"]:
            parsed = None
        if parsed is None:
            warning_key = (source, repr(value))
            with _INVALID_SETTING_WARNINGS_LOCK:
                first = warning_key not in _INVALID_SETTING_WARNINGS
                _INVALID_SETTING_WARNINGS.add(warning_key)
            if first:
                logger.warning("Invalid %s value %r; using default %r.", source, value, default)
            return default
        return max(spec["minimum"], min(spec["maximum"], parsed)) if "minimum" in spec else parsed
