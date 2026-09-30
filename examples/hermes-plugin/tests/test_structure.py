"""Structure rules of the plugin, checked on the source files.

The static tests parse every plugin module with ``ast`` and never import it:

- ``core/*`` does not import the plugin package (``__init__``) or ``_setup``.
- Only ``core/host.py`` imports Hermes packages. ``__init__.py`` and ``_setup.py``
  keep a pinned set of call-time Hermes imports until they move behind
  ``core/host.py``; a new one fails here.
- Code that runs at import time (module and class bodies, decorators, default
  values) registers no atexit hook, starts no thread, touches no network and reads
  no configuration or environment.
- ``mcp``, ``httpx2`` and the rest of the MCP client stack are not imported at
  import time.
- Hermes discovery finds the provider by the text ``MemoryProvider`` in the first
  8 KB of ``__init__.py`` (hermes:plugins/memory/__init__.py
  ``_is_memory_provider_dir``).

One runtime test loads the plugin through the external loader fixture and checks
that the load registers no atexit hook and starts no thread.
"""

import ast
import atexit
import threading
from pathlib import Path

import pytest

_PLUGIN = Path(__file__).resolve().parents[1]
_CORE = _PLUGIN / "core"
_MODULES = sorted(
    [_PLUGIN / "__init__.py", _PLUGIN / "_setup.py", *_CORE.glob("*.py")],
    key=lambda path: path.relative_to(_PLUGIN).as_posix(),
)
_HOST = _CORE / "host.py"

# Top-level Hermes packages and modules a plugin could import.
_HERMES_ROOTS = {
    "acp_adapter",
    "agent",
    "cli",
    "cron",
    "gateway",
    "model_tools",
    "plugins",
    "pm",
    "run_agent",
    "tools",
    "toolsets",
    "tui_gateway",
    "utils",
}

# Call-time Hermes imports outside core/, as (file, imported module). They are
# scheduled to move behind core/host.py; do not add to this set.
_PINNED_HERMES_IMPORTS = {
    ("__init__.py", "hermes_cli.config"),
    ("__init__.py", "hermes_constants"),
    ("_setup.py", "hermes_cli.config"),
    ("_setup.py", "hermes_cli.curses_ui"),
    ("_setup.py", "hermes_cli.memory_setup"),
    ("_setup.py", "hermes_constants"),
}

# Modules that must only be imported on the call path.
_LAZY_ROOTS = {"anyio", "httpx", "httpx2", "mcp", "mcp_types"}

_ATEXIT = {"atexit.register"}
_THREAD_START = {
    "_thread.start_new",
    "_thread.start_new_thread",
    "asyncio.run",
    "concurrent.futures.ThreadPoolExecutor",
    "multiprocessing.Process",
    "threading.Thread",
    "threading.Timer",
}
_THREAD_START_NAMES = {"spawn_context_thread", "start"}
_NETWORK_ROOTS = {"http", "httpx", "httpx2", "mcp", "requests", "socket", "subprocess"}
_NETWORK = {"urllib.request.urlopen", "urllib.request.Request"}
_NETWORK_NAMES = {"connect", "create_connection", "getaddrinfo", "urlopen"}
_CONFIG_READS = {"open", "os.getenv", "os.environ.get", "json.load", "io.open"}
_CONFIG_READ_NAMES = {
    "get_hermes_home",
    "get_secret",
    "load_config",
    "read_bytes",
    "read_text",
}


def _name(path):
    return path.relative_to(_PLUGIN).as_posix()


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _is_hermes(module):
    root = module.split(".")[0]
    return root in _HERMES_ROOTS or root.startswith("hermes_")


def _dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def _is_type_checking(test):
    return (_dotted(test) or "").rpartition(".")[2] == "TYPE_CHECKING"


class _ImportTime(ast.NodeVisitor):
    """Collect the imports and calls that run when the module is imported.

    Function and lambda bodies run later and are skipped; their decorators and
    default values run at definition time and are visited. ``if TYPE_CHECKING:``
    bodies never run.
    """

    def __init__(self):
        self.aliases = {}
        self.imports = []
        self.calls = []
        self.subscripts = []

    def visit_FunctionDef(self, node):
        for child in node.decorator_list + node.args.defaults:
            self.visit(child)
        for child in node.args.kw_defaults:
            if child is not None:
                self.visit(child)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        for child in node.args.defaults:
            self.visit(child)

    def visit_If(self, node):
        if not _is_type_checking(node.test):
            self.visit(node.test)
            for child in node.body:
                self.visit(child)
        for child in node.orelse:
            self.visit(child)

    def visit_Import(self, node):
        for alias in node.names:
            self.imports.append((node.lineno, alias.name))
            if alias.asname:
                self.aliases[alias.asname] = alias.name
            else:
                root = alias.name.split(".")[0]
                self.aliases[root] = root

    def visit_ImportFrom(self, node):
        module = node.module or ""
        if node.level == 0:
            self.imports.append((node.lineno, module))
        prefix = "." * node.level + module
        for alias in node.names:
            target = f"{prefix}.{alias.name}" if module else f"{prefix}{alias.name}"
            self.aliases[alias.asname or alias.name] = target

    def visit_Call(self, node):
        dotted = _dotted(node.func)
        if dotted is None and isinstance(node.func, ast.Attribute):
            dotted = "<expr>." + node.func.attr
        if dotted is not None:
            head, _, rest = dotted.partition(".")
            resolved = self.aliases.get(head, head)
            self.calls.append((node.lineno, f"{resolved}.{rest}" if rest else resolved))
        self.generic_visit(node)

    def visit_Subscript(self, node):
        dotted = _dotted(node.value)
        if dotted is not None:
            head, _, rest = dotted.partition(".")
            resolved = self.aliases.get(head, head)
            self.subscripts.append((node.lineno, f"{resolved}.{rest}" if rest else resolved))
        self.generic_visit(node)


def _import_time(path):
    visitor = _ImportTime()
    visitor.visit(_tree(path))
    return visitor


def _every_import(path):
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, 0, alias.name, [alias.name]
        elif isinstance(node, ast.ImportFrom):
            yield node.lineno, node.level, node.module or "", [a.name for a in node.names]


def _matches(call, exact, names=(), roots=()):
    last = call.rpartition(".")[2]
    return call in exact or last in names or call.split(".")[0] in roots


@pytest.mark.parametrize("path", sorted(_CORE.glob("*.py")), ids=_name)
def test_core_does_not_import_the_plugin_package_or_setup(path):
    for line, level, module, names in _every_import(path):
        where = f"{_name(path)}:{line}"
        assert level <= 1, f"{where} imports from the plugin package"
        if level == 1:
            assert module != "_setup" and not (module == "" and "_setup" in names), where


@pytest.mark.parametrize("path", _MODULES, ids=_name)
def test_only_core_host_imports_hermes(path):
    for line, level, module, _ in _every_import(path):
        if level or not _is_hermes(module) or path == _HOST:
            continue
        where = f"{_name(path)}:{line}"
        assert path.parent == _PLUGIN, f"{where} imports Hermes module {module}"
        assert (_name(path), module) in _PINNED_HERMES_IMPORTS, (
            f"{where} imports Hermes module {module}; import it through core/host.py"
        )
    for line, module in _import_time(path).imports:
        if path != _HOST:
            assert not _is_hermes(module), f"{_name(path)}:{line} imports {module} at import time"


def test_pinned_hermes_imports_still_exist():
    found = {
        (_name(path), module)
        for path in (_PLUGIN / "__init__.py", _PLUGIN / "_setup.py")
        for _, level, module, _ in _every_import(path)
        if not level and _is_hermes(module)
    }
    assert found == _PINNED_HERMES_IMPORTS


@pytest.mark.parametrize("path", _MODULES, ids=_name)
def test_import_time_code_registers_no_exit_hook_starts_no_thread_and_opens_no_connection(path):
    for line, call in _import_time(path).calls:
        where = f"{_name(path)}:{line} calls {call} at import time"
        assert not _matches(call, _ATEXIT, roots={"atexit"}), where
        assert not _matches(call, _THREAD_START, _THREAD_START_NAMES), where
        assert not _matches(call, _NETWORK, _NETWORK_NAMES, _NETWORK_ROOTS), where
        assert not _matches(call, {"importlib.import_module", "__import__"}), where


@pytest.mark.parametrize("path", _MODULES, ids=_name)
def test_import_time_code_reads_no_configuration(path):
    visitor = _import_time(path)
    for line, call in visitor.calls:
        assert not _matches(call, _CONFIG_READS, _CONFIG_READ_NAMES), (
            f"{_name(path)}:{line} calls {call} at import time"
        )
    for line, value in visitor.subscripts:
        assert value != "os.environ", f"{_name(path)}:{line} reads os.environ at import time"


@pytest.mark.parametrize("path", _MODULES, ids=_name)
def test_mcp_stack_is_imported_lazily(path):
    for line, module in _import_time(path).imports:
        assert module.split(".")[0] not in _LAZY_ROOTS, (
            f"{_name(path)}:{line} imports {module} at import time"
        )


def test_discovery_marker_is_in_the_first_8_kb_of_init():
    head = (_PLUGIN / "__init__.py").read_text(errors="replace", encoding="utf-8-sig")[:8192]
    assert "MemoryProvider" in head


def test_loading_the_plugin_registers_no_exit_hook_and_starts_no_thread(
    external_provider, monkeypatch
):
    registered, started = [], []
    start = threading.Thread.start

    def record_start(thread):
        started.append(thread.name)
        return start(thread)

    monkeypatch.setattr(atexit, "register", lambda *args, **kwargs: registered.append(args))
    monkeypatch.setattr(threading.Thread, "start", record_start)
    callbacks = atexit._ncallbacks()
    before = set(threading.enumerate())

    _, provider, module, _ = external_provider("structure")

    assert module.__name__.startswith("_hermes_user_memory.")
    assert type(provider).__module__ == module.__name__
    assert registered == []
    assert atexit._ncallbacks() == callbacks
    assert started == []
    assert set(threading.enumerate()) - before == set()
