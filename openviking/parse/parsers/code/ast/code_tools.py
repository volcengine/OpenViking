# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Pure I/O-free formatters powering the code_outline / code_search / code_expand
MCP tools. Inputs are source strings; outputs are agent-facing text.

The MCP layer handles all URI resolution and I/O; this module deals only with
content -> CodeSkeleton -> formatted text.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Tuple

from openviking.parse.parsers.code.ast.extractor import get_extractor
from openviking.parse.parsers.code.ast.skeleton import (
    CodeSkeleton,
    FunctionSig,
    _compact_params,
)


def _line_span(item) -> str:
    if item.line_start and item.line_end:
        return f"  L{item.line_start}-{item.line_end}"
    return ""


def _format_function(fn: FunctionSig, indent: str, prefix: str) -> str:
    ret = f" -> {fn.return_type}" if fn.return_type else ""
    params = _compact_params(fn.params)
    return f"{indent}{prefix}{fn.name}({params}){ret}{_line_span(fn)}"


def _outline_text(skeleton: CodeSkeleton, total_lines: int) -> str:
    lines: List[str] = [f"{skeleton.file_name}  [{skeleton.language}, {total_lines} lines]"]
    if skeleton.module_doc:
        first = skeleton.module_doc.split("\n", 1)[0].strip()
        if first:
            lines.append(f'module: "{first}"')
    if skeleton.imports:
        lines.append(f"imports: {', '.join(skeleton.imports)}")
    lines.append("")

    for cls in skeleton.classes:
        bases = f"({', '.join(cls.bases)})" if cls.bases else ""
        lines.append(f"class {cls.name}{bases}{_line_span(cls)}")
        for method in cls.methods:
            lines.append(_format_function(method, "  ", "+ "))
        lines.append("")

    for fn in skeleton.functions:
        lines.append(_format_function(fn, "", "def "))

    return "\n".join(lines).rstrip()


def outline_file(content: str, file_name: str) -> str:
    """Return outline view of one source file (header + symbols + line spans).

    Returns an "Error: ..." sentinel string when the language is unsupported or
    parsing fails — callers can detect by the "Error:" prefix.
    """
    skeleton = get_extractor().extract(file_name, content)
    if skeleton is None:
        return _failure_message(file_name)
    total_lines = content.count("\n") + 1 if content else 0
    return _outline_text(skeleton, total_lines)


def _failure_message(file_name: str) -> str:
    if not get_extractor().supports(file_name):
        return f"Error: unsupported language for {file_name}"
    return f"Error: failed to parse {file_name}"


def _iter_symbols(skeleton: CodeSkeleton) -> Iterable[Tuple[str, int, int]]:
    """Yield (display_name, line_start, line_end) for every symbol."""
    for cls in skeleton.classes:
        yield cls.name, cls.line_start, cls.line_end
        for method in cls.methods:
            yield f"{cls.name}.{method.name}", method.line_start, method.line_end
    for fn in skeleton.functions:
        yield fn.name, fn.line_start, fn.line_end


def search_symbols(query: str, files: List[Tuple[str, str]]) -> str:
    """Case-insensitive substring search across symbol names in many files.

    files: list of (content, file_name) tuples. Files whose language is
    unsupported or fails to parse are silently skipped (the caller already
    filtered by extension).
    """
    if not query:
        return "Error: empty query"

    needle = query.lower()
    extractor = get_extractor()
    scanned = 0
    hits_by_file: List[Tuple[str, List[Tuple[str, int, int]]]] = []
    total = 0

    for content, file_name in files:
        scanned += 1
        skeleton = extractor.extract(file_name, content)
        if skeleton is None:
            continue
        file_hits: List[Tuple[str, int, int]] = []
        for name, start, end in _iter_symbols(skeleton):
            tail = name.rsplit(".", 1)[-1]
            haystack = name.lower() if "." in needle else tail.lower()
            if needle in haystack:
                file_hits.append((name, start, end))
        if file_hits:
            hits_by_file.append((file_name, file_hits))
            total += len(file_hits)

    if total == 0:
        return f'No matches for "{query}" (scanned {scanned} files)'

    out: List[str] = [f'{total} matches for "{query}" (scanned {scanned} files)']
    for file_name, file_hits in hits_by_file:
        out.append("")
        out.append(file_name)
        for name, start, end in file_hits:
            span = f"  L{start}-{end}" if start and end else ""
            out.append(f"  {name}{span}")
    return "\n".join(out)


def _resolve_symbol(
    skeleton: CodeSkeleton, symbol: str
) -> Optional[Tuple[str, int, int]]:
    """Find a symbol by 'foo' (bare) or 'Foo.bar' (qualified). Case sensitive.

    Search priority for bare names (no dot):
      1. Top-level functions  — exact name match
      2. Classes              — exact name match
      3. Methods in any class — bare method name, first class that contains it wins
         (returns qualified display name "ClassName.method" so the caller knows
          where the method lives; use 'Foo.bar' to target a specific class)
    """
    if "." in symbol:
        cls_name, method_name = symbol.split(".", 1)
        for cls in skeleton.classes:
            if cls.name == cls_name:
                for method in cls.methods:
                    if method.name == method_name:
                        return f"{cls.name}.{method.name}", method.line_start, method.line_end
        return None

    for fn in skeleton.functions:
        if fn.name == symbol:
            return fn.name, fn.line_start, fn.line_end
    for cls in skeleton.classes:
        if cls.name == symbol:
            return cls.name, cls.line_start, cls.line_end
        for method in cls.methods:
            if method.name == symbol:
                return f"{cls.name}.{method.name}", method.line_start, method.line_end
    return None


def expand_symbol(content: str, file_name: str, symbol: str) -> str:
    """Return the source for `symbol` from `content`, with a location header.

    Accepts 'foo' (any function/class/method named foo, first match wins) or
    'Foo.bar' (method bar inside class Foo).
    """
    skeleton = get_extractor().extract(file_name, content)
    if skeleton is None:
        return _failure_message(file_name)

    match = _resolve_symbol(skeleton, symbol)
    if match is None:
        return f"Error: symbol '{symbol}' not found in {file_name}"

    display_name, start, end = match
    if not start or not end:
        return f"Error: symbol '{symbol}' found but line numbers unavailable in {file_name}"

    lines = content.splitlines()
    body = "\n".join(lines[start - 1 : end])
    return f"# {file_name}  L{start}-{end}  ({display_name})\n\n{body}"
