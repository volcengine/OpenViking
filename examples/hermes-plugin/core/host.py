"""Every Hermes import of the plugin and its version-compat branches.

No other core module imports ``agent``, ``hermes_cli``, ``hermes_constants``,
``tools``, ``tui_gateway`` or ``utils``. The names below are imported with the
module, as the plugin always did. The names in ``_LAZY`` were imported inside
functions; ``from .host import <name>`` inside a function still imports them at
that moment, through the module ``__getattr__``, and a failed import raises
``ImportError`` there as before. Nothing is cached, so a patch on the Hermes
module reaches the next call.
"""

from __future__ import annotations

from agent.memory_provider import MemoryProvider, spawn_context_thread
from agent.message_content import flatten_message_text
from agent.secret_scope import get_secret
from agent.skill_commands import extract_user_instruction_from_skill_message
from hermes_cli import __version__ as _HERMES_VERSION
from hermes_constants import get_hermes_home, get_process_hermes_home
from tools.registry import tool_error
from utils import atomic_json_write, env_var_enabled

try:
    from hermes_constants import get_routing_process_hermes_home as _get_launch_hermes_home
except ImportError:  # Hermes releases before process-home pinning
    _get_launch_hermes_home = get_process_hermes_home

try:
    from agent.memory_provider import RecallStatus
except ImportError:  # Hermes releases before the recall indicator
    RecallStatus = None

# name -> Hermes module, imported on use.
_LAZY = {
    "raise_if_read_blocked": "agent.file_safety",
    "build_profile_secret_scope": "agent.secret_scope",
    "is_multiplex_active": "agent.secret_scope",
    "reset_secret_scope": "agent.secret_scope",
    "set_secret_scope": "agent.secret_scope",
    "load_config": "hermes_cli.config",
    "load_config_readonly": "hermes_cli.config",
    "save_config": "hermes_cli.config",
    "hydrate_profile_secret_sources": "hermes_cli.env_loader",
    "mkdir_under_hermes_home": "hermes_constants",
    "reset_hermes_home_override": "hermes_constants",
    "set_hermes_home_override": "hermes_constants",
    "is_always_blocked_url": "tools.url_safety",
    "launch_profile_policy": "tui_gateway",
}

__all__ = [
    "MemoryProvider",
    "RecallStatus",
    "_HERMES_VERSION",
    "_get_launch_hermes_home",
    "atomic_json_write",
    "env_var_enabled",
    "extract_user_instruction_from_skill_message",
    "flatten_message_text",
    "get_hermes_home",
    "get_process_hermes_home",
    "get_secret",
    "spawn_context_thread",
    "tool_error",
]


def __getattr__(name: str):
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    # Same resolution as ``from <module> import <name>``, including submodules.
    return getattr(__import__(module, fromlist=[name]), name)
