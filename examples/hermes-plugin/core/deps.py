"""``Deps``: every outside dependency of the plugin as one injectable value."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from .health import (
    _classify_runtime_openviking_health,
    _validate_openviking_reachability,
    _validate_openviking_setup_values,
)
from .http import _get_httpx, _VikingClient
from .ovcli import _discover_ovcli_profiles


@dataclass(frozen=True)
class Deps:
    """Everything the plugin reaches outside the process through, as one injectable value.

    Each provider holds one (``self._deps``) and passes it to the module-level
    helpers it calls. Helpers called without a provider read ``default_deps()``.
    Tests swap a dependency by building a Deps (``dataclasses.replace``) rather
    than patching names in this module, so the fake still reaches the code that
    reads it after that code moves to another module.
    """

    # REST transport factory: returns an object with httpx's get/post/delete, or None without httpx.
    transport: Callable[[], Any] = _get_httpx
    # REST client factory, called like ``_VikingClient`` without ``transport``.
    # None builds a ``_VikingClient`` over ``transport()``.
    client: Optional[Callable[..., Any]] = None
    # MCP session factory; unused until the MCP bridge is wired in.
    mcp_session: Optional[Callable[..., Any]] = None
    monotonic: Callable[[], float] = time.monotonic
    wall: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    # (client, endpoint) -> ("healthy" | "responded" | "unreachable", message)
    health: Callable[[Any, str], tuple[str, str]] = _classify_runtime_openviking_health
    # Used by the setup wizard.
    discover_profiles: Callable[[], List[Any]] = _discover_ovcli_profiles
    validate_reachability: Callable[[str], tuple[bool, str]] = _validate_openviking_reachability
    validate_setup_values: Callable[..., tuple[bool, str, Optional[str]]] = _validate_openviking_setup_values


_default_deps = Deps()


def default_deps() -> Deps:
    """The Deps a new provider starts from, also read by helpers called without a provider."""
    return _default_deps


def set_default_deps(deps: Deps) -> Deps:
    """Replace the module default and return the previous one.

    This is the one supported way to swap a dependency plugin-wide; tests restore
    the returned value afterwards. Providers constructed earlier keep their own Deps.
    """
    global _default_deps
    previous, _default_deps = _default_deps, deps
    return previous


def _rest_client(deps: Deps, endpoint: str, api_key: str = "", **identity) -> "_VikingClient":
    """A REST client from ``deps``: its client factory, or ``_VikingClient`` over its transport."""
    if deps.client is not None:
        return deps.client(endpoint, api_key, **identity)
    return _VikingClient(endpoint, api_key, transport=deps.transport(), **identity)
