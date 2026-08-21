"""Tests for Compile read tracing (audit hook, minimal version).

Verifies that files actually opened by ``exec`` subprocesses via Python land in
the readlist, while pure enumeration (``glob``) does not, and that the change
never breaks the exec pipeline (non-Compile sessions untouched).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import vikingbot.compile.readtrace as readtrace
from vikingbot.compile.readlist import READLIST_PATH, ReadlistTracker
from vikingbot.sandbox.backends.direct import DirectBackend
from vikingbot.sandbox.base import SandboxFileInfo
from vikingbot.config.schema import SandboxBackend, SandboxConfig, SandboxMode

TRACE_DIR = readtrace.TRACE_DIR
READLIST_ENV = "READLIST_FILE"


def _read_env(workspace: Path) -> dict[str, str]:
    """Env equivalent to what DirectBackend injects for a Compile exec."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(TRACE_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    env[READLIST_ENV] = str(workspace / READLIST_PATH)
    env["READLIST_WORKSPACE"] = str(workspace)
    return env


def _readlist_lines(workspace: Path) -> list[str]:
    path = workspace / READLIST_PATH
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line]


def _make_source(workspace: Path, rel: str, content: str = "hello world\n") -> Path:
    path = workspace / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_audit_hook_records_files_opened_by_python(workspace):
    src = _make_source(workspace, "compile_resources/src_2/a.jsonl")
    proc = subprocess.run(
        [sys.executable, "-c", f"open({str(src)!r}, 'a').close()"],
        cwd=workspace,
        env=_read_env(workspace),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "compile_resources/src_2/a.jsonl" in _readlist_lines(workspace)


def test_audit_hook_ignores_glob_enumeration(workspace):
    _make_source(workspace, "compile_resources/src_2/a.jsonl", "x\n")
    _make_source(workspace, "compile_resources/src_2/b.jsonl", "y\n")
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import glob; print(len(glob.glob('compile_resources/**/*.jsonl', recursive=True)))",
        ],
        cwd=workspace,
        env=_read_env(workspace),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert _readlist_lines(workspace) == []  # glob only enumerates, does not read


def test_audit_hook_records_relative_open_after_chdir(workspace):
    src = _make_source(workspace, "compile_resources/src_2/dream-sessions/07/02/x.jsonl")
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import os; os.chdir({str(src.parent)!r}); open('x.jsonl', 'a').close()",
        ],
        cwd=workspace,
        env=_read_env(workspace),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert (
        "compile_resources/src_2/dream-sessions/07/02/x.jsonl" in _readlist_lines(workspace)
    )


def test_audit_hook_noops_without_readlist_env(workspace):
    _make_source(workspace, "compile_resources/src_2/a.jsonl", "x\n")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(TRACE_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    env.pop(READLIST_ENV, None)  # no READLIST_FILE: hook must not install
    proc = subprocess.run(
        [sys.executable, "-c", "open('compile_resources/src_2/a.jsonl', 'a').close()"],
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert _readlist_lines(workspace) == []


@pytest.mark.asyncio
async def test_readlist_tracker_preserves_externally_appended_paths():
    class Sandbox:
        def __init__(self):
            self.files: dict[str, str] = {}

        async def list_files(self, max_entries=None):
            del max_entries
            return [SandboxFileInfo(path="compile_resources/src_1/a.md", size=1)]

        async def read_file(self, path):
            return self.files.get(path, "")

        async def write_file(self, path, content):
            self.files[path] = content

    sandbox = Sandbox()
    tracker = ReadlistTracker(sandbox=sandbox)
    await tracker.initialize()

    await tracker.record(["compile_resources/src_1/a.md"])
    # Simulate a readtrace subprocess appending a line the tracker never saw.
    sandbox.files[READLIST_PATH] = (
        sandbox.files.get(READLIST_PATH, "") + "compile_resources/src_2/b.jsonl\n"
    )
    await tracker.record(["compile_resources/src_1/c.md"])

    lines = set(sandbox.files[READLIST_PATH].splitlines())
    assert "compile_resources/src_1/a.md" in lines
    assert "compile_resources/src_2/b.jsonl" in lines
    assert "compile_resources/src_1/c.md" in lines


async def _run_direct(workspace_id: str, workspace: Path) -> DirectBackend:
    backend = DirectBackend(
        SandboxConfig(backend=SandboxBackend.DIRECT, mode=SandboxMode.PER_SESSION),
        workspace_id,
        workspace,
    )
    await backend.start()
    return backend


@pytest.mark.asyncio
async def test_direct_backend_injects_read_env_for_compile_session(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / "compile_resources/src_2").mkdir(parents=True, exist_ok=True)
    (workspace / "compile_resources/src_2/a.jsonl").write_text("x\n", encoding="utf-8")
    backend = await _run_direct("compile__cmp_task__cmp_task", workspace)
    result = await backend.execute(
        "python3 -c \"open('compile_resources/src_2/a.jsonl', 'a').close()\""
    )
    assert "Error" not in result
    lines = _readlist_lines(workspace)
    assert "compile_resources/src_2/a.jsonl" in lines


@pytest.mark.asyncio
async def test_direct_backend_does_not_inject_for_non_compile_session(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / "compile_resources/src_2").mkdir(parents=True, exist_ok=True)
    (workspace / "compile_resources/src_2/a.jsonl").write_text("x\n", encoding="utf-8")
    backend = await _run_direct("cli__default__chat", workspace)
    result = await backend.execute(
        "python3 -c \"open('compile_resources/src_2/a.jsonl', 'a').close()\""
    )
    assert "Error" not in result
    assert _readlist_lines(workspace) == []


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    return ws
