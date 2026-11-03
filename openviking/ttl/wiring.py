# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Process-wide registry and service wiring for the TTL adapter.

The adapter is a singleton per server process. ``app.py`` creates the built-in
implementation from ``server.ttl_adapter``, registers it here and installs its
post-write hook on the VikingFS instance. The admin configuration routes consult
:func:`ttl_config_supported` to decide whether an explicit TTL section is
allowed or must be rejected with 501. When no adapter is registered
(default configuration) every TTL entry point is inert.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping, Optional, Sequence

from openviking_cli.utils.logger import get_logger

if TYPE_CHECKING:
    from openviking.server.config import TTLAdapterConfig
    from openviking.server.identity import RequestContext
    from openviking.storage.viking_fs import VikingFS
    from openviking.ttl.adapter import TTLAdapter

logger = get_logger(__name__)

_adapter: Optional["TTLAdapter"] = None


def build_ttl_adapter(config: Optional["TTLAdapterConfig"]) -> Optional["TTLAdapter"]:
    """Create the built-in Redis TTL implementation only when enabled."""
    if config is None or not config.enabled:
        return None
    from openviking.ttl.runtime import DirectoryTTL

    return DirectoryTTL(**config.config)


def set_ttl_adapter(adapter: Optional["TTLAdapter"]) -> None:
    """Register (or clear) the process-wide TTL adapter."""
    global _adapter
    _adapter = adapter


def get_ttl_adapter() -> Optional["TTLAdapter"]:
    """Return the registered TTL adapter, or ``None`` when TTL is off."""
    return _adapter


def ttl_config_supported() -> bool:
    """Return True when explicit TTL configuration writes are allowed.

    False when the Redis TTL implementation is not enabled, so admin routes
    return 501 / ``UNIMPLEMENTED`` for an explicit TTL section.
    """
    return _adapter is not None and _adapter.supports_config()


def install_ttl_hook(viking_fs: "VikingFS", adapter: Optional["TTLAdapter"]) -> None:
    """Install the adapter's post-write hook, or leave the default-off hook.

    Called once after the VikingFS instance exists. When no adapter is present
    the hook stays unset, so a committed write never reads the clock or does any
    TTL I/O (default-off behavior).
    """
    if adapter is None:
        return
    viking_fs.set_post_write_hook(adapter.on_post_write)
    logger.info("Installed TTL post-write hook")


def _entry_uri(entry: Any) -> Optional[str]:
    if isinstance(entry, Mapping):
        uri = entry.get("uri")
        return uri if isinstance(uri, str) and uri else None
    return None


async def attach_expires_at(
    entries: Sequence[Any],
    ctx: Optional["RequestContext"],
) -> None:
    """Default-off read-path ``expires_at`` echo-back (see the technical design).

    Attaches ``expires_at`` in place to each dict entry that carries a ``uri``;
    a single ``stat`` result is just a one-element sequence. No-op in the
    default configuration (no adapter), so the read path does zero TTL I/O. When a
    Redis TTL implementation is enabled it computes the value from the current
    effective policy; the field is only written for URIs the adapter actually
    covers (value string or ``None``), so the kernel never fabricates a field
    the product layer did not compute. Any adapter failure degrades to "no echo
    this page" instead of disturbing the underlying listing/stat result.
    """
    adapter = _adapter
    if adapter is None or not entries:
        return
    uris = list(dict.fromkeys(u for u in (_entry_uri(e) for e in entries) if u is not None))
    if not uris:
        return
    try:
        resolved = await adapter.resolve_expires_at(uris, ctx)
    except Exception:  # noqa: BLE001 - echo-back must never break stat/ls/tree
        logger.exception("TTL adapter resolve_expires_at() failed; omitting expires_at echo")
        return
    for entry in entries:
        uri = _entry_uri(entry)
        if uri is not None and uri in resolved:
            entry["expires_at"] = resolved[uri]


__all__ = [
    "attach_expires_at",
    "get_ttl_adapter",
    "install_ttl_hook",
    "set_ttl_adapter",
    "ttl_config_supported",
]
