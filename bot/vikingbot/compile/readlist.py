"""Read tracking for Compile tasks (optimization B: readlist).

The readlist records every workspace file whose content has already entered the
agent's context, so the per-iteration reminder can steer the model toward unread
files instead of re-opening the same sources. The state lives in a sandbox file
(``__compile_staging__/tmp/readlist.md``) rather than only in the conversation, so
it survives ``_compact_tool_loop`` and stays independent of chat history.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from loguru import logger

from vikingbot.agent.tools.base import Tool
from vikingbot.compile.models import COMPILE_MANIFEST_NAME, COMPILE_MATERIALIZED_ROOT

READLIST_PATH = "__compile_staging__/tmp/readlist.md"
_READLIST_UNIVERSE_MAX_ENTRIES = 200_000
_RECENT_LIMIT = 10
_UNREAD_SAMPLE_LIMIT = 10
_SHELL_SPLIT_RE = re.compile(r"""[\s;|&<>()'"`]+""")


def _normalize_workspace_path(path: str) -> str | None:
    """Normalize a workspace path to the form used by the sandbox file APIs.

    Rejects absolute/escaping paths and returns ``None`` for empty input so callers
    can skip them without raising.
    """
    value = str(path or "").strip()
    if not value:
        return None
    candidate = value
    while candidate.startswith("./") or candidate.startswith("/"):
        candidate = candidate[1:]
    if not candidate or ".." in candidate.split("/"):
        return None
    return candidate


class ReadlistTracker:
    """Tracks which workspace files have been read and renders a compact summary."""

    def __init__(self, *, sandbox: Any):
        self._sandbox = sandbox
        self._universe: set[str] = set()
        self._read: set[str] = set()
        self._read_order: list[str] = []
        self._lock = asyncio.Lock()

    @property
    def read_paths(self) -> set[str]:
        return set(self._read)

    @property
    def universe(self) -> set[str]:
        return set(self._universe)

    async def initialize(self, *, universe: set[str] | None = None) -> None:
        """Load the candidate source files and any pre-existing readlist content."""
        if universe is not None:
            self._universe = set(universe)
        elif not self._universe:
            self._universe = await self._discover_universe()
        await self._reload()

    async def _discover_universe(self) -> set[str]:
        """Derive the readable source files from the materialized tree."""
        try:
            entries = await self._sandbox.list_files(max_entries=_READLIST_UNIVERSE_MAX_ENTRIES)
        except Exception as exc:
            logger.warning("[READLIST]: list_files failed: {}", exc)
            return set()
        prefix = f"{COMPILE_MATERIALIZED_ROOT}/"
        return {
            entry.path
            for entry in entries
            if entry.path.startswith(prefix) and not entry.path.endswith(
                f"/{COMPILE_MANIFEST_NAME}"
            )
        }

    async def _reload(self) -> None:
        """Merge externally-appended paths (e.g. from an exec traversal script)."""
        try:
            content = await self._sandbox.read_file(READLIST_PATH)
        except Exception:
            return
        for line in str(content or "").splitlines():
            path = _normalize_workspace_path(line)
            if path and path not in self._read:
                self._read.add(path)
                self._read_order.append(path)

    async def record(self, paths: Any) -> None:
        """Record one or more workspace paths as read (idempotent, best-effort)."""
        new_paths: list[str] = []
        for raw in paths:
            path = _normalize_workspace_path(raw)
            if not path or path in self._read:
                continue
            self._read.add(path)
            self._read_order.append(path)
            new_paths.append(path)
        if not new_paths:
            return
        async with self._lock:
            try:
                # Merge lines appended externally (e.g. by the readtrace audit
                # hook / PATH shims inside exec subprocesses) since the last
                # load, so this full rewrite cannot drop reads the tracker never
                # saw itself.
                await self._reload()
                await self._sandbox.write_file(
                    READLIST_PATH,
                    "\n".join(self._read_order) + "\n",
                )
            except Exception as exc:
                logger.warning("[READLIST]: write failed: {}", exc)

    async def summary(self, *, recent_limit: int = _RECENT_LIMIT) -> str:
        """Render the compact per-iteration read/unread note (or ``""`` when empty)."""
        await self._reload()
        if not self._universe:
            return ""
        read = self._read & self._universe
        read_count = len(read)
        total = len(self._universe)
        unread = sorted(self._universe - self._read)
        unread_count = len(unread)
        if read_count == 0:
            return f"源文件共 {total} 个，尚未读取任何源文件；优先去读未读文件。"
        lines = [f"已读 {read_count}/{total} 个源文件，未读 {unread_count} 个。"]
        recent = [path for path in self._read_order if path in self._universe][-recent_limit:]
        if recent:
            lines.append("最近已读: " + ", ".join(recent) + "。")
        if 0 < unread_count <= _UNREAD_SAMPLE_LIMIT:
            lines.append("未读: " + ", ".join(unread) + "。")
        lines.append("这些已读文件的内容已进入你的上下文，不必再读；优先去读未读文件。")
        return "\n".join(lines)


def _extract_exec_paths(command: str, universe: set[str]) -> set[str]:
    """Extract explicit file paths referenced in a shell command.

    Only paths that already exist in ``universe`` are kept, which makes the heuristic
    safe against commands that mention flags or non-file tokens. Inline ``python -c``
    scripts that open files via ``glob``/``open`` are intentionally not parsed; the
    readlist fallback convention covers those.
    """
    found: set[str] = set()
    if not universe:
        return found
    for token in _SHELL_SPLIT_RE.split(command):
        path = _normalize_workspace_path(token)
        if path and path in universe:
            found.add(path)
    return found


class ReadTrackingTool(Tool):
    """Wrap a workspace read tool and record the files it opened into a readlist."""

    def __init__(self, tool: Tool, *, tracker: ReadlistTracker):
        self._tool = tool
        self._tracker = tracker

    @property
    def name(self) -> str:
        return self._tool.name

    @property
    def description(self) -> str:
        return self._tool.description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._tool.parameters

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        return self._tool.validate_params(params)

    async def execute(self, tool_context: Any, **kwargs: Any) -> str:
        result = await self._tool.execute(tool_context, **kwargs)
        await self._record_reads(self._tool.name, kwargs, result)
        return result

    async def _record_reads(self, name: str, kwargs: dict[str, Any], result: Any) -> None:
        if isinstance(result, str) and result.lstrip().startswith("Error"):
            return
        paths: set[str] = set()
        if name in {"read_file", "edit_file"}:
            path = kwargs.get("path")
            if path:
                paths.add(str(path))
        elif name == "exec":
            command = str(kwargs.get("command") or "")
            paths.update(_extract_exec_paths(command, self._tracker.universe))
        if paths:
            await self._tracker.record(paths)


__all__ = [
    "READLIST_PATH",
    "ReadTrackingTool",
    "ReadlistTracker",
]
