#!/usr/bin/env python3
"""Run tests/parse/test_code_tools.py without the full openviking dependency stack.

Uses the same stub-then-load pattern as tests/mcp_benchmark/server.py.
"""
import importlib.util
import logging
import sys
import types
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent

# Stub openviking_cli so extractor.py imports cleanly.
ovc = types.ModuleType("openviking_cli")
ovc_utils = types.ModuleType("openviking_cli.utils")
ovc_utils.get_logger = lambda name: logging.getLogger(name)
sys.modules["openviking_cli"] = ovc
sys.modules["openviking_cli.utils"] = ovc_utils

# Stub the openviking package hierarchy so __init__.py files are skipped.
for pkg in (
    "openviking",
    "openviking.parse",
    "openviking.parse.parsers",
    "openviking.parse.parsers.code",
    "openviking.parse.parsers.code.ast",
    "openviking.parse.parsers.code.ast.languages",
):
    m = types.ModuleType(pkg)
    m.__path__ = [str(ROOT / pkg.replace(".", "/"))]
    sys.modules[pkg] = m


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_load("openviking.parse.parsers.code.ast.skeleton",
      "openviking/parse/parsers/code/ast/skeleton.py")
_load("openviking.parse.parsers.code.ast.languages.base",
      "openviking/parse/parsers/code/ast/languages/base.py")
for lang in ("python", "js_ts", "go", "rust", "java", "cpp", "csharp", "php", "lua"):
    _load(f"openviking.parse.parsers.code.ast.languages.{lang}",
          f"openviking/parse/parsers/code/ast/languages/{lang}.py")
_load("openviking.parse.parsers.code.ast.extractor",
      "openviking/parse/parsers/code/ast/extractor.py")
_load("openviking.parse.parsers.code.ast.code_tools",
      "openviking/parse/parsers/code/ast/code_tools.py")

if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([
        str(Path(__file__).parent / "test_code_tools.py"),
        "--noconftest",
        "-o", "addopts=",
        "-v",
    ] + sys.argv[1:]))
