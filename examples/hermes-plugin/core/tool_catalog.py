"""The ``openviking_*`` tool catalogue: which server MCP tools Hermes registers.

Hermes calls ``get_tool_schemas()`` before ``initialize()`` and fixes its routing
table from that first answer, so the catalogue must be available without a
session. Sources, in order: an in-process cache keyed by the connection
fingerprint, a disk cache holding the last successful ``tools/list`` with its
endpoint, a live ``tools/list`` under a short budget, and otherwise nothing.
There is no shipped snapshot: the server owns the tool descriptions.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from .log import get_logger

logger = get_logger()

TOOL_PREFIX = "openviking_"
LIVE_LIST_BUDGET_SECONDS = 3.0
DEFAULT_EXPOSED_TOOLS = (
    "find", "search", "read", "list", "tree", "grep", "glob",
    "remember", "forget", "add_resource", "health",
)
# Exposed only when the ``extra_tools`` setting lists them.
OPTIONAL_TOOLS = ("write", "edit", "add_skill", "list_watches", "cancel_watch")
RECALL_TOOLS = ("find", "search", "read", "list", "tree", "grep", "glob")
RECALL_TOOL_NAMES = frozenset(TOOL_PREFIX + name for name in RECALL_TOOLS)
CACHE_RELATIVE_PATH = Path("openviking") / "tools_cache.json"

_memory_cache: Dict[str, List[Dict[str, Any]]] = {}
_memory_lock = threading.Lock()
_warned: set = set()


def parse_extra_tools(value: Any) -> tuple:
    """The optional server tools a setting value asks for (comma list or list), unknown names dropped."""
    items = value if isinstance(value, (list, tuple)) else str(value or "").split(",")
    names = []
    for item in items:
        name = str(item or "").strip().lower()
        if name.startswith(TOOL_PREFIX):
            name = name[len(TOOL_PREFIX):]
        if name in OPTIONAL_TOOLS and name not in names:
            names.append(name)
    return tuple(names)


def exposed_server_names(extra: Iterable[str] = ()) -> frozenset:
    return frozenset(DEFAULT_EXPOSED_TOOLS) | frozenset(n for n in extra if n in OPTIONAL_TOOLS)


def server_name(hermes_name: str) -> str:
    """``openviking_find`` -> ``find``; empty for a name outside the namespace."""
    return hermes_name[len(TOOL_PREFIX):] if hermes_name.startswith(TOOL_PREFIX) else ""


def to_hermes_schema(tool: Dict[str, Any]) -> Dict[str, Any]:
    """One server tool as a Hermes tool schema; description and input schema as the server sent them."""
    name = tool["name"]
    parameters = copy.deepcopy(tool.get("inputSchema") or {"type": "object", "properties": {}})
    if name == "forget":
        # Hermes deletes exact memory files only; the wrapper forces recursive=false.
        properties = parameters.get("properties")
        if isinstance(properties, dict):
            properties.pop("recursive", None)
        if isinstance(parameters.get("required"), list):
            parameters["required"] = [r for r in parameters["required"] if r != "recursive"]
    return {"name": TOOL_PREFIX + name, "description": tool.get("description") or "", "parameters": parameters}


def build_schemas(tools: Iterable[Dict[str, Any]], extra: Iterable[str] = ()) -> List[Dict[str, Any]]:
    exposed = exposed_server_names(extra)
    seen: set = set()
    schemas = []
    for tool in tools or []:
        name = tool.get("name") if isinstance(tool, dict) else None
        if name in exposed and name not in seen:
            seen.add(name)
            schemas.append(to_hermes_schema(tool))
    return schemas


def fingerprint(endpoint: str, api_key: str = "", account: str = "", user: str = "", agent: str = "") -> str:
    raw = "\0".join((endpoint.rstrip("/"), api_key, account, user, agent))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def cache_path(hermes_home: str | os.PathLike) -> Path:
    return Path(hermes_home) / CACHE_RELATIVE_PATH


def read_disk_cache(hermes_home: str | os.PathLike, endpoint: str) -> Optional[List[Dict[str, Any]]]:
    try:
        data = json.loads(cache_path(hermes_home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("endpoint") != endpoint.rstrip("/"):
        return None
    tools = data.get("tools")
    if not isinstance(tools, list):
        return None
    return [t for t in tools if isinstance(t, dict) and isinstance(t.get("name"), str)]


def write_disk_cache(hermes_home: str | os.PathLike, endpoint: str, tools: List[Dict[str, Any]]) -> None:
    path = cache_path(hermes_home)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps({"endpoint": endpoint.rstrip("/"), "tools": tools}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        logger.debug("OpenViking tool cache write failed: %s", exc)


def remember(key: str, hermes_home: str, endpoint: str, tools: List[Dict[str, Any]]) -> None:
    """Store a successful ``tools/list`` in both caches."""
    with _memory_lock:
        _memory_cache[key] = list(tools)
    if hermes_home:
        write_disk_cache(hermes_home, endpoint, tools)


def load(
    key: str,
    hermes_home: str,
    endpoint: str,
    fetch: Callable[[float], List[Dict[str, Any]]],
    *,
    budget: float = LIVE_LIST_BUDGET_SECONDS,
) -> List[Dict[str, Any]]:
    """Raw server tools from the first source that has them; ``fetch(budget)`` is the live ``tools/list``."""
    with _memory_lock:
        cached = _memory_cache.get(key)
    if cached is not None:
        return list(cached)
    if hermes_home:
        disk = read_disk_cache(hermes_home, endpoint)
        if disk is not None:
            with _memory_lock:
                _memory_cache.setdefault(key, disk)
            return list(disk)
    try:
        tools = fetch(budget)
    except Exception as exc:
        if key not in _warned:
            _warned.add(key)
            logger.warning("OpenViking tools unavailable for this session: %s", exc)
        return []
    remember(key, hermes_home, endpoint, tools)
    return list(tools)


def clear_memory_cache() -> None:
    with _memory_lock:
        _memory_cache.clear()
    _warned.clear()
