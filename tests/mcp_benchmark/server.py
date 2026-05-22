"""Minimal stdio MCP server that exposes the 3 new code-navigation tools
backed by the *actual* code_tools / extractor modules from the OpenViking
repo. Stubs the heavy openviking dependencies (pyagfs, etc.) and maps
viking:// URIs to local disk so we can drive an end-to-end MCP test
without building the full openviking stack.

URI scheme:
  viking://local/<ABSOLUTE-PATH>   -> /<ABSOLUTE-PATH> on local disk

This is *only* for the end-to-end MCP verification. Production runs through
openviking.server.mcp_endpoint with the real VikingFS.
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import sys
import types
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent.parent.parent

# Stub openviking_cli.utils.get_logger so extractor.py imports cleanly.
ovc = types.ModuleType("openviking_cli")
ovc_utils = types.ModuleType("openviking_cli.utils")
ovc_utils.get_logger = lambda name: logging.getLogger(name)
sys.modules["openviking_cli"] = ovc
sys.modules["openviking_cli.utils"] = ovc_utils

for pkg in (
    "openviking",
    "openviking.parse",
    "openviking.parse.parsers",
    "openviking.parse.parsers.code",
    "openviking.parse.parsers.code.ast",
    "openviking.parse.parsers.code.ast.languages",
):
    m = types.ModuleType(pkg)
    m.__path__ = []
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
_load("openviking.parse.parsers.code.ast.languages.python",
      "openviking/parse/parsers/code/ast/languages/python.py")
_load("openviking.parse.parsers.code.ast.extractor",
      "openviking/parse/parsers/code/ast/extractor.py")
_load("openviking.parse.parsers.code.ast.code_tools",
      "openviking/parse/parsers/code/ast/code_tools.py")

from openviking.parse.parsers.code.ast.code_tools import (  # noqa: E402
    expand_symbol,
    outline_file,
    search_symbols,
)
from openviking.parse.parsers.code.ast.extractor import get_extractor  # noqa: E402

from mcp.server.fastmcp import FastMCP  # noqa: E402

logger = logging.getLogger("openviking_mcp_test")

mcp = FastMCP("openviking-code-tools-test")

_VIKING_PREFIX = "viking://local"
_CODE_SEARCH_FILE_CAP = 200
_CODE_SEARCH_CONCURRENCY = 10


def _uri_to_path(uri: str) -> Optional[Path]:
    """Map viking://local/<abs-path> -> Path('<abs-path>'). None on bad URI."""
    if not isinstance(uri, str) or not uri.startswith(_VIKING_PREFIX):
        return None
    rest = uri[len(_VIKING_PREFIX):]
    if not rest.startswith("/"):
        return None
    return Path(rest)


def _require_viking_uri(uri: str) -> Optional[str]:
    if not isinstance(uri, str) or not uri.startswith("viking://"):
        return (
            "Error: only viking:// URIs are supported; "
            "use add_resource to ingest local code as a viking:// resource first."
        )
    return None


async def _read_text(uri: str) -> tuple[Optional[str], Optional[str]]:
    """Returns (content, error_msg). Content is None iff error_msg is set."""
    path = _uri_to_path(uri)
    if path is None:
        return None, f"Error: {uri} is not a viking://local URI in this test server"
    try:
        return await asyncio.to_thread(path.read_text, encoding="utf-8"), None
    except FileNotFoundError as exc:
        return None, f"Error: failed to read {uri}: {exc}"
    except UnicodeDecodeError:
        return None, f"Error: {uri} is not text"
    except Exception as exc:
        return None, f"Error: failed to read {uri}: {exc}"


async def _walk(uri: str) -> tuple[Optional[list[str]], Optional[str]]:
    """Recursively list viking:// URIs under a directory. Returns (uris, error)."""
    root = _uri_to_path(uri)
    if root is None or not root.is_dir():
        return None, f"Error: failed to list {uri}: not a directory"
    found: list[str] = []
    try:
        def _scan() -> list[str]:
            out: list[str] = []
            for p in root.rglob("*"):
                if p.is_file():
                    out.append(f"{_VIKING_PREFIX}{p}")
            return out
        found = await asyncio.to_thread(_scan)
        return found, None
    except Exception as exc:
        return None, f"Error: failed to list {uri}: {exc}"


@mcp.tool()
async def code_outline(uri: str) -> str:
    """Show a file's symbol structure — classes, functions, methods, and their line ranges.
    Returns a structural map without reading implementation bodies.

    Use to survey a file before deciding what to read. More efficient than reading the whole
    file when you only need to locate a method or understand a file's API surface.
    Typical workflow: code_search → code_outline → code_expand.

    uri must be a viking://local file URI in this test server."""
    err = _require_viking_uri(uri)
    if err:
        return err
    content, err = await _read_text(uri)
    if err:
        return err
    return outline_file(content, uri)


@mcp.tool()
async def code_search(query: str, uri: str) -> str:
    """Search symbol names (class / function / method) by substring across a viking://local directory.
    Returns structured results: symbol type, class context, file URI, and line range.

    Use when you don't know which file contains the symbol you're looking for. Returns
    structural context (class ownership, location) that raw text search does not provide.

    Skip if you already know the exact file; use code_outline or Read directly.

    Scans up to 200 source files. Narrow uri to a subdirectory for deeper coverage."""
    err = _require_viking_uri(uri)
    if err:
        return err
    if not query:
        return "Error: empty query"

    all_uris, err = await _walk(uri)
    if err:
        return err

    extractor = get_extractor()
    code_uris: list[str] = []
    for u in all_uris or []:
        if extractor.supports(u):
            code_uris.append(u)
            if len(code_uris) >= _CODE_SEARCH_FILE_CAP:
                break

    if not code_uris:
        return f"No supported source files found under {uri}"

    capped = len(code_uris) >= _CODE_SEARCH_FILE_CAP
    sem = asyncio.Semaphore(_CODE_SEARCH_CONCURRENCY)

    async def _fetch(u: str):
        async with sem:
            body, ferr = await _read_text(u)
            if ferr:
                logger.warning("code_search: %s", ferr)
                return None
            return body, u

    fetched = await asyncio.gather(*[_fetch(u) for u in code_uris])
    files = [pair for pair in fetched if pair is not None]
    result = search_symbols(query, files)
    if capped:
        result += "\n\n(scanning stopped at 200-file cap; narrow uri to search more)"
    return result


@mcp.tool()
async def code_expand(uri: str, symbol: str) -> str:
    """Return the full source of a single named symbol (function, class, or method).
    Reads only that symbol's body, avoiding the overhead of reading an entire file.

    Use when you need the implementation of one specific symbol. For reading multiple
    symbols from the same file, Read is often more efficient.

    `symbol` accepts 'bar' (top-level) or 'Foo.bar' (method).
    uri must be a viking://local file URI in this test server."""
    err = _require_viking_uri(uri)
    if err:
        return err
    if not symbol:
        return "Error: empty symbol"
    content, err = await _read_text(uri)
    if err:
        return err
    return expand_symbol(content, uri, symbol)


if __name__ == "__main__":
    mcp.run()
