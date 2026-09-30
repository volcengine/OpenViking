"""Server health classification and the setup wizard's validators."""

from __future__ import annotations

from typing import Optional

from .endpoint import _normalize_openviking_url, _OpenVikingEndpointError
from .http import (
    _OPENVIKING_IDENTIFIED_STATES,
    _format_openviking_exception,
    _OpenVikingHTTPError,
    _probe_openviking_identity,
    _status_code_from_error,
    _VikingClient,
)
from .local_server import _local_listener_suffix
from .settings import _DEFAULT_AGENT, _clean_config_value

_OPENVIKING_RESPONDED_FAILURE_PREFIX = "OpenViking server responded"
_LEGACY_OPENVIKING_IDENTITY_DETAIL = (
    "returned OpenViking's legacy health response, but its anonymous OpenAPI metadata did not identify OpenViking. "
    "If this is OpenViking 0.2.6 or earlier, upgrade to OpenViking 0.2.14 or newer."
)


def _identity_failure(identity: str, subject: str, *, unhealthy_status: str = "status", legacy_subject: Optional[str] = None) -> str:
    """Human message for a non-identified probe result, or "" when identified."""
    if identity in _OPENVIKING_IDENTIFIED_STATES:
        return ""
    return {
        "unhealthy": f"{subject} responded but reported unhealthy {unhealthy_status}.",
        "legacy-unverified": f"{legacy_subject or subject} {_LEGACY_OPENVIKING_IDENTITY_DETAIL}",
    }.get(identity, f"{subject} responded, but its /health response is not valid OpenViking.")


def _client_health_failure(client, subject: str, **identity_kwargs) -> Optional[str]:
    """"" when healthy, a message when the server answered but is not healthy OpenViking,
    None when a payload-less (test double) client's health() is simply False."""
    if hasattr(client, "health_payload"):
        return _identity_failure(_probe_openviking_identity(client)[0], subject, **identity_kwargs)
    return "" if client.health() else None


def _validate_openviking_reachability(endpoint: str) -> tuple[bool, str]:
    endpoint = _normalize_openviking_url(endpoint)
    try:
        message = _client_health_failure(_VikingClient(endpoint), "OpenViking server", legacy_subject="The server")
        if message is not None:
            return (not message), message
    except Exception as e:
        if _status_code_from_error(e) is not None:
            return False, f"OpenViking server responded with {_format_openviking_exception(e)}."
        return False, f"OpenViking server is not reachable at {endpoint}: {_format_openviking_exception(e)}"
    return False, f"OpenViking server is not reachable at {endpoint}."


def _validate_openviking_setup_values(values: dict, *, require_api_key: bool = False) -> tuple[bool, str, Optional[str]]:
    """-> (ok, message, role) where role is 'root' / 'user' / None (no key)."""
    try:
        endpoint = _normalize_openviking_url(values.get("endpoint"))
    except _OpenVikingEndpointError as exc:
        return False, str(exc), None
    api_key = _clean_config_value(values.get("api_key"))
    if require_api_key and not api_key:
        return False, "Remote OpenViking configs require an API key.", None
    account = _clean_config_value(values.get("account"))
    user = _clean_config_value(values.get("user"))
    account, user = account or "default", user or "default"
    try:
        client = _VikingClient(endpoint, api_key, account=account, user=user,
                               agent=_clean_config_value(values.get("agent")) or _DEFAULT_AGENT)
        identity, health = _probe_openviking_identity(client)
        if identity == "invalid":
            return False, "Server /health response is not valid OpenViking.", None
        message = _identity_failure(identity, "OpenViking server", legacy_subject="The server")
        if message:
            return False, message, None
        if require_api_key or api_key or health.get("auth_mode") in {"api_key", "trusted", None}:
            client.validate_auth()
        if not api_key:
            return True, "", None
        try:
            client.validate_root_access()
            return True, "", "root"
        except Exception as e:
            if _status_code_from_error(e) in {401, 403, 404}:
                return True, "", "user"
            raise
    except Exception as e:
        return False, f"OpenViking validation failed: {_format_openviking_exception(e)}", None


def _classify_runtime_openviking_health(client: _VikingClient, endpoint: str) -> tuple[str, str]:
    """-> ("healthy" | "responded" | "unreachable", message). A false health result is
    not treated as server absence unless nothing answered at all."""
    subject = f"Service at {endpoint}"
    try:
        message = _client_health_failure(client, subject, unhealthy_status="OpenViking status")
        if message is not None:
            return ("healthy", "") if not message else ("responded", message + _local_listener_suffix(endpoint))
    except _OpenVikingHTTPError as e:
        return "responded", f"{subject} responded with {_format_openviking_exception(e)}.{_local_listener_suffix(endpoint)}"
    except Exception:
        pass
    return "unreachable", ""
