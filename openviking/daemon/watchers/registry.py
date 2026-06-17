"""
Watcher registry for creating tool-specific watchers by name.
"""
from typing import Dict, Type

from openviking.daemon.watchers import BaseWatcher
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

# Registry mapping tool names to watcher classes
_WATCHER_REGISTRY: Dict[str, Type] = {}


def register_watcher(tool_name: str):
    """Decorator to register a watcher class for a tool name."""
    def decorator(cls):
        _WATCHER_REGISTRY[tool_name] = cls
        return cls
    return decorator


def create_watcher(tool_name: str, **kwargs) -> BaseWatcher:
    """Factory: create a watcher instance by tool name."""
    cls = _WATCHER_REGISTRY.get(tool_name)
    if cls is None:
        available = list(_WATCHER_REGISTRY.keys())
        raise ValueError(f"Unknown watcher tool: '{tool_name}'. Available: {available}")
    return cls(**kwargs)


def list_available_watchers() -> list:
    """Return list of registered watcher tool names."""
    return list(_WATCHER_REGISTRY.keys())


def _register_builtins():
    """Register built-in watchers. Called lazily to avoid import cycles."""
    if _WATCHER_REGISTRY:
        return
    try:
        from openviking.daemon.watchers.claude_code_watcher import ClaudeCodeWatcher
        _WATCHER_REGISTRY["claude_code"] = ClaudeCodeWatcher
    except ImportError:
        pass

    try:
        from openviking.daemon.watchers.generic_jsonl_watcher import GenericJSONLWatcher
        _WATCHER_REGISTRY["generic_jsonl"] = GenericJSONLWatcher
    except ImportError:
        pass

    try:
        from openviking.daemon.watchers.aider_watcher import AiderWatcher
        _WATCHER_REGISTRY["aider"] = AiderWatcher
    except ImportError:
        pass

    try:
        from openviking.daemon.watchers.cursor_watcher import CursorWatcher
        _WATCHER_REGISTRY["cursor"] = CursorWatcher
    except ImportError:
        pass

    try:
        from openviking.daemon.watchers.continue_dev_watcher import ContinueDevWatcher
        _WATCHER_REGISTRY["continue_dev"] = ContinueDevWatcher
    except ImportError:
        pass


_register_builtins()
