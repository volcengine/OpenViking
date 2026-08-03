from __future__ import annotations

import ast
import os
import subprocess
import sys
from importlib import import_module
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = PROJECT_ROOT / "integrations" / "langchain"
SOURCE_ROOT = PACKAGE_ROOT / "src"
SDK_ROOT = PROJECT_ROOT / "sdk" / "python"


def test_server_distribution_does_not_depend_on_standalone_integration():
    root_pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    root_lock = (PROJECT_ROOT / "uv.lock").read_text(encoding="utf-8")

    assert "langchain-openviking" not in root_pyproject
    assert 'name = "langchain-openviking"' not in root_lock


def test_standalone_package_has_no_server_imports_at_module_scope():
    violations: list[str] = []
    for source_file in sorted((SOURCE_ROOT / "langchain_openviking").glob("*.py")):
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
assert langchain_openviking.has_request_actor_peer_support()
"""

    subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_base_star_import_omits_optional_middleware(tmp_path):
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(SOURCE_ROOT), str(SDK_ROOT)))
    probe = """
import importlib.util

real_find_spec = importlib.util.find_spec
importlib.util.find_spec = (
    lambda name, *args, **kwargs:
    None if name == "langchain" else real_find_spec(name, *args, **kwargs)
)

import langchain_openviking

assert "OpenVikingContextMiddleware" not in langchain_openviking.__all__
namespace = {}
exec("from langchain_openviking import *", namespace)
assert "OpenVikingContextMiddleware" not in namespace
assert "OpenVikingRetriever" in namespace
"""

    subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_legacy_namespace_remains_importable_without_standalone_package(tmp_path):
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(PROJECT_ROOT), str(SDK_ROOT)))
    probe = """
import importlib.abc
import importlib
import sys


class BlockStandalonePackage(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "langchain_openviking" or fullname.startswith("langchain_openviking."):
            raise ModuleNotFoundError(
                "No module named 'langchain_openviking'",
                name="langchain_openviking",
            )
        return None


sys.meta_path.insert(0, BlockStandalonePackage())

import openviking.integrations.langchain as legacy

assert "OpenVikingRetriever" in legacy.__all__
assert isinstance(legacy.has_request_actor_peer_support(), bool)

try:
    legacy.OpenVikingRetriever
except ImportError as exc:
    message = str(exc)
    assert "langchain-openviking" in message
    assert "openviking[langchain]" not in message
else:
    raise AssertionError("missing standalone package should produce an installation error")

for submodule in (
    "actor_peer",
    "client",
    "context",
    "history",
    "messages",
    "middleware",
    "recording",
    "retrievers",
    "store",
    "testing",
    "tools",
):
    try:
        importlib.import_module(f"openviking.integrations.langchain.{submodule}")
    except ImportError as exc:
        assert not isinstance(exc, ModuleNotFoundError)
        message = str(exc)
        assert "langchain-openviking" in message
        assert "openviking[langchain]" not in message
    else:
        raise AssertionError(f"legacy {submodule} import should produce an installation error")
"""

    subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_legacy_imports_resolve_to_canonical_objects():
    pytest.importorskip("langchain_core")
    pytest.importorskip("langchain_openviking")
    canonical = import_module("langchain_openviking")
    legacy = import_module("openviking.integrations.langchain")

    for name in canonical.__all__:
        assert getattr(legacy, name) is getattr(canonical, name)

    for submodule in (
        "actor_peer",
        "client",
        "context",
        "history",
        "messages",
        "middleware",
        "recording",
        "retrievers",
        "store",
        "testing",
        "tools",
    ):
        legacy_module = import_module(f"openviking.integrations.langchain.{submodule}")
        canonical_module = import_module(f"langchain_openviking.{submodule}")
        legacy_public = {name for name in vars(legacy_module) if not name.startswith("_")}
        canonical_public = {name for name in vars(canonical_module) if not name.startswith("_")}

        assert legacy_public == canonical_public
        for name in sorted(canonical_public):
            assert getattr(legacy_module, name) is getattr(canonical_module, name)


@pytest.mark.parametrize(
    ("uri", "expected_parts", "expected_scope", "expected_content_index"),
    [
        (
            "viking://user/memories/store/data/users/ada.json",
            ("user", "memories", "store", "data", "users", "ada.json"),
            "user",
            1,
        ),
        (
            "viking://user/default/memories/store/index/users/ada.md",
            ("user", "default", "memories", "store", "index", "users", "ada.md"),
            "user",
            2,
        ),
        (
            "viking://user/default/peers/assistant/resources/store/data.json",
            ("user", "default", "peers", "assistant", "resources", "store", "data.json"),
            "user",
            4,
        ),
        (
            "viking://user/peers/assistant/memories/store/index.md",
            ("user", "peers", "assistant", "memories", "store", "index.md"),
            "user",
            3,
        ),
        (
            "viking://agent/default/skills/review/SKILL.md",
            ("agent", "default", "skills", "review", "SKILL.md"),
            "agent",
            2,
        ),
        (
            "viking://resources/shared.md",
            ("resources", "shared.md"),
            "resources",
            None,
        ),
        (
            "viking://user/default/sessions/thread-1",
            ("user", "default", "sessions", "thread-1"),
            "user",
            None,
        ),
        (
            "/user/default/memories/store/data.json?profile=1",
            ("user", "default", "memories", "store", "data.json"),
            "user",
            2,
        ),
    ],
)
def test_private_uri_classification_preserves_server_namespace_shapes(
    uri,
    expected_parts,
    expected_scope,
    expected_content_index,
):
    pytest.importorskip("langchain_openviking")
    from langchain_openviking._uri import classify_uri

    classification = classify_uri(uri)

    assert classification.parts == expected_parts
    assert classification.scope == expected_scope
    assert classification.content_index == expected_content_index
