"""OpenViking endpoint normalization, labels and the SSRF floor check."""

from __future__ import annotations

from contextlib import suppress
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

from .log import get_logger
from .settings import _DEFAULT_ENDPOINT, _clean_config_value

logger = get_logger()


_LOCAL_OPENVIKING_HOSTS = {"localhost", "127.0.0.1", "::1"}


class _OpenVikingEndpointError(ValueError):
    """Raised when a configured endpoint cannot be used safely."""


def _openviking_endpoint_label(value: Any) -> str:
    """Credential-free endpoint label for logs and UI."""
    raw = _clean_config_value(value)
    if not raw:
        return "<empty endpoint>"
    try:
        parsed = urlparse(raw if "://" in raw else f"//{raw}")
        host = parsed.hostname
        if not host:
            return "<configured endpoint>"
        display_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        port = None
        with suppress(ValueError):
            port = parsed.port
        return f"{parsed.scheme + '://' if parsed.scheme else ''}{display_host}{f':{port}' if port is not None else ''}"
    except Exception:
        return "<configured endpoint>"


@lru_cache(maxsize=128)
def _openviking_endpoint_is_always_blocked(candidate: str) -> bool:
    """SSRF floor check, cached per endpoint value: the live provider re-resolves settings
    on every access (Dashboard / ``/reload``), so slow DNS lookups stay off the hot path."""
    from .host import is_always_blocked_url

    return is_always_blocked_url(candidate)


def _normalize_openviking_url(url: str) -> str:
    trimmed = _clean_config_value(url).rstrip("/")
    if not trimmed:
        return _DEFAULT_ENDPOINT
    lower = trimmed.lower()
    if lower in {"localhost", "127.0.0.1"}:
        candidate = f"http://{trimmed}:1933"
    elif lower in {"::1", "[::1]"}:
        candidate = "http://[::1]:1933"
    elif lower.startswith(("[::1]:", "::1:")):
        candidate = f"http://[::1]:{trimmed.rsplit(':', 1)[1]}"
    else:
        candidate = trimmed if "://" in trimmed else f"http://{trimmed}"
    try:
        parsed = urlparse(candidate)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise ValueError("OpenViking endpoints must use http:// or https:// with a host.")
        parsed.port  # urlparse defers malformed-port validation to this access
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("OpenViking endpoints cannot contain user info, query parameters, or fragments.")
    except ValueError as exc:
        raise _OpenVikingEndpointError(f"Invalid OpenViking endpoint {_openviking_endpoint_label(candidate)}: {exc}") from exc

    # Local/LAN self-host stays allowed; reject cloud-metadata floors so a poisoned
    # endpoint cannot SSRF via memory sync. Never silently substitute localhost for
    # an unsafe endpoint — that could forward credentials to the wrong deployment.
    try:
        blocked = _openviking_endpoint_is_always_blocked(candidate)
    except Exception as exc:
        logger.debug("OpenViking endpoint safety validation failed", exc_info=True)
        raise _OpenVikingEndpointError("OpenViking endpoint safety validation failed; Hermes refused the connection.") from exc
    if blocked:
        raise _OpenVikingEndpointError(
            f"OpenViking endpoint {_openviking_endpoint_label(candidate)} targets a blocked metadata address."
        )
    return candidate


def _is_local_openviking_url(value: str) -> bool:
    try:
        candidate = _normalize_openviking_url(value)
    except _OpenVikingEndpointError:
        return False
    parsed = urlparse(candidate)
    return parsed.scheme.lower() == "http" and (parsed.hostname or "").lower() in _LOCAL_OPENVIKING_HOSTS
