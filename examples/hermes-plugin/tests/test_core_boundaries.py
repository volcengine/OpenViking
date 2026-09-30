"""Import rules of the plugin's ``core`` package.

No core module imports the plugin package (``__init__``/``_setup``); only
``core/host.py`` imports Hermes; importing the core modules registers no atexit
hook, starts no thread, opens no socket and loads neither the MCP SDK nor httpx.
"""

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1] / "core"
_HERMES = {"agent", "hermes_cli", "hermes_constants", "tools", "tui_gateway", "utils"}


def _imports(path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield 0, alias.name
        elif isinstance(node, ast.ImportFrom):
            yield node.level, node.module or ""


@pytest.mark.parametrize("path", sorted(_CORE.glob("*.py")), ids=lambda p: p.name)
def test_core_module_imports_neither_the_package_nor_hermes(path):
    for level, module in _imports(path):
        assert level < 2, f"{path.name} imports the plugin package"
        if level == 0 and path.name != "host.py":
            assert module.split(".")[0] not in _HERMES, f"{path.name} imports Hermes module {module}"


def test_importing_the_core_modules_has_no_side_effects():
    script = f"""
import atexit, importlib, importlib.machinery, importlib.util, socket, sys, threading
# Hermes and the stdlib may register hooks of their own; load them before counting.
import agent.memory_provider, agent.message_content, agent.secret_scope, agent.skill_commands
import hermes_cli, hermes_constants, tools.registry, utils
def refuse(*args, **kwargs):
    raise AssertionError("network touched during import")
socket.socket.connect = refuse
socket.create_connection = refuse
spec = importlib.machinery.ModuleSpec("ov_plugin", None, is_package=True)
spec.submodule_search_locations = [{str(_CORE.parent)!r}]
sys.modules["ov_plugin"] = importlib.util.module_from_spec(spec)
callbacks, threads = atexit._ncallbacks(), threading.active_count()
for name in {sorted(p.stem for p in _CORE.glob("*.py") if p.stem != "__init__")!r}:
    importlib.import_module("ov_plugin.core." + name)
leaked = sorted(m for m in sys.modules if m.split(".")[0] in ("mcp", "mcp_types", "httpx", "httpx2", "anyio"))
assert not leaked, leaked
assert threading.active_count() == threads, threading.enumerate()
assert atexit._ncallbacks() == callbacks
print("clean")
"""
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60, env=env
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "clean"
