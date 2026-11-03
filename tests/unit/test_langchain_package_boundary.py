from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = PROJECT_ROOT / "integrations" / "frameworks" / "langchain"
SOURCE_ROOT = PACKAGE_ROOT / "src"
SDK_ROOT = PROJECT_ROOT / "sdk" / "python"


def _repository_files(root: Path) -> list[Path]:
    try:
        git_root = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=PROJECT_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if Path(git_root).resolve() == PROJECT_ROOT.resolve():
            names = subprocess.check_output(
                [
                    "git",
                    "ls-files",
                    "-z",
                    "--cached",
                    "--others",
                    "--exclude-standard",
                    "--",
                    str(root.relative_to(PROJECT_ROOT)),
                ],
                cwd=PROJECT_ROOT,
                text=True,
                stderr=subprocess.DEVNULL,
            ).split("\0")
            candidates = [PROJECT_ROOT / name for name in names if name]
        else:
            candidates = list(root.rglob("*"))
    except (OSError, subprocess.CalledProcessError):
        # Source archives have no Git metadata; still check their package layout.
        candidates = list(root.rglob("*"))
    return [
        path
        for path in candidates
        if "__pycache__" not in path.relative_to(PROJECT_ROOT).parts
        and (path.is_file() or path.is_symlink())
    ]


def test_server_distribution_does_not_depend_on_standalone_integration():
    root_pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    root_lock = (PROJECT_ROOT / "uv.lock").read_text(encoding="utf-8")

    assert "langchain-openviking" not in root_pyproject
    assert 'name = "langchain-openviking"' not in root_lock


def test_server_source_excludes_removed_framework_namespace():
    stale_paths = sorted(
        str(path.relative_to(PROJECT_ROOT))
        for path in _repository_files(PROJECT_ROOT / "openviking" / "integrations")
    )
    assert not stale_paths, "Remove legacy framework files: " + ", ".join(stale_paths)


def test_framework_sources_and_tests_use_package_locations():
    retired_roots = [
        PROJECT_ROOT / "tests" / "integration" / "langchain_langgraph",
        PROJECT_ROOT / "examples" / "agent-framework",
        PROJECT_ROOT / "examples" / "langchain",
        PROJECT_ROOT / "examples" / "langchain-langgraph",
    ]
    stale_files = [path for root in retired_roots for path in _repository_files(root)]
    stale_files.extend(
        path
        for path in (PROJECT_ROOT / "tests" / "unit").glob("test_langchain_*.py")
        if path.name != "test_langchain_package_boundary.py"
    )
    stale_paths = sorted(str(path.relative_to(PROJECT_ROOT)) for path in stale_files)
    assert not stale_paths, (
        "Move framework sources and tests into integrations/frameworks/: " + ", ".join(stale_paths)
    )


def test_standalone_package_has_no_server_imports_at_module_scope():
    violations: list[str] = []
    source_files = sorted((SOURCE_ROOT / "langchain_openviking").glob("*.py"))
    assert source_files, "Standalone package source must be available for the boundary check"
    for source_file in source_files:
        tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "openviking" or module.startswith("openviking."):
                    violations.append(f"{source_file.name}:{node.lineno}: from {module}")
                if module == "openviking_cli" or module.startswith("openviking_cli."):
                    violations.append(f"{source_file.name}:{node.lineno}: from {module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "openviking" or alias.name.startswith("openviking."):
                        violations.append(f"{source_file.name}:{node.lineno}: import {alias.name}")
                    if alias.name == "openviking_cli" or alias.name.startswith("openviking_cli."):
                        violations.append(f"{source_file.name}:{node.lineno}: import {alias.name}")

        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "import_module"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                continue
            module = node.args[0].value
            is_full_package = module == "openviking" or module.startswith("openviking.")
            is_cli_package = module == "openviking_cli" or module.startswith("openviking_cli.")
            if is_full_package or is_cli_package:
                violations.append(f"{source_file.name}:{node.lineno}: import_module({module!r})")

    assert violations == []


def test_standalone_package_import_does_not_load_full_openviking(tmp_path):
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(SOURCE_ROOT), str(SDK_ROOT)))
    probe = """
import sys
import langchain_openviking

assert "openviking" not in sys.modules
assert not any(name.startswith("openviking_cli") for name in sys.modules)
"""

    subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
