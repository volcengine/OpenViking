# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""CLI output helpers."""

import json
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Optional

import typer
from tabulate import tabulate

from openviking.cli.context import CLIContext

_MAX_COL_WIDTH = 80


def _to_serializable(value: Any) -> Any:
    """Convert rich Python values to JSON-serializable primitives."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "to_dict"):
        return _to_serializable(value.to_dict())
    if is_dataclass(value):
        return _to_serializable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _to_serializable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_serializable(item) for item in value]
    return str(value)


def _truncate(val: Any) -> Any:
    """Truncate a value to _MAX_COL_WIDTH for table display."""
    s = str(val) if not isinstance(val, str) else val
    return s[: _MAX_COL_WIDTH - 3] + "..." if len(s) > _MAX_COL_WIDTH else val


def _format_list_table(rows: List[Dict[str, Any]]) -> Optional[str]:
    """Render a list of dict rows as a table with truncation."""
    if not rows:
        return None
    headers: List[str] = []
    for row in rows:
        for key in row.keys():
            key_str = str(key)
            if key_str not in headers:
                headers.append(key_str)
    if not headers:
        return None
    values = [[_truncate(row.get(h, "")) for h in headers] for row in rows]
    return tabulate(values, headers=headers, tablefmt="plain")


def _is_primitive_list(v: Any) -> bool:
    return isinstance(v, list) and v and all(isinstance(r, (str, int, float, bool)) for r in v)


def _is_dict_list(v: Any) -> bool:
    return isinstance(v, list) and v and all(isinstance(r, dict) for r in v)


def _to_table(data: Any) -> Optional[str]:
    """Try to render data as a table. Returns None if not possible."""
    # Rule 1: list[dict] -> multi-row table
    if isinstance(data, list) and data and all(isinstance(r, dict) for r in data):
        return _format_list_table(data)

    if not isinstance(data, dict):
        return None

    # Rule 5: ComponentStatus (name + is_healthy + status)
    if {"name", "is_healthy", "status"}.issubset(data):
        health = "healthy" if data["is_healthy"] else "unhealthy"
        return f"[{data['name']}] ({health})\n{data['status']}"

    # Rule 6: SystemStatus (is_healthy + components)
    if "components" in data and "is_healthy" in data:
        lines: List[str] = []
        for comp in data["components"].values():
            table = _to_table(comp)
            if table:
                lines.append(table)
                lines.append("")
        health = "healthy" if data["is_healthy"] else "unhealthy"
        lines.append(f"[system] ({health})")
        if data.get("errors"):
            lines.append(f"Errors: {', '.join(data['errors'])}")
        return "\n".join(lines)

    # Extract list fields
    dict_lists = {k: v for k, v in data.items() if _is_dict_list(v)}
    prim_lists = {k: v for k, v in data.items() if _is_primitive_list(v)}

    # Rule 3a: single list[primitive] -> one item per line
    if not dict_lists and len(prim_lists) == 1:
        key, items = next(iter(prim_lists.items()))
        col = key.rstrip("es") if key.endswith("es") else key.rstrip("s")
        return _format_list_table([{col: item} for item in items])

    # Rule 3b: single list[dict] -> render directly
    if len(dict_lists) == 1 and not prim_lists:
        return _format_list_table(next(iter(dict_lists.values())))

    # Rule 2: multiple list[dict] -> flatten with type column
    if dict_lists:
        merged: List[Dict[str, Any]] = []
        for key, items in dict_lists.items():
            for item in items:
                merged.append({"type": key.rstrip("s"), **item})
        if merged:
            return _format_list_table(merged)

    # Rule 4: plain dict (no expandable lists) -> single-row horizontal table
    if not dict_lists and not prim_lists:
        return tabulate(
            [[_truncate(v) for v in data.values()]], headers=data.keys(), tablefmt="plain"
        )

    return None


def output_success(ctx: CLIContext, result: Any) -> None:
    """Print successful command result."""
    serializable = _to_serializable(result)

    if ctx.json_output:
        typer.echo(json.dumps({"ok": True, "result": serializable}, ensure_ascii=False))
        return
    if serializable is None:
        return
    if isinstance(serializable, str):
        typer.echo(serializable)
        return

    if ctx.output_format == "table":
        table = _to_table(serializable)
        if table is not None:
            typer.echo(table)
            return

    typer.echo(json.dumps(serializable, ensure_ascii=False, indent=2))


def output_error(
    ctx: CLIContext,
    *,
    message: str,
    code: str,
    exit_code: int,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """Print error in JSON or plain format then exit."""
    details = details or {}
    if ctx.json_output:
        payload = {
            "ok": False,
            "error": {
                "code": code,
                "message": message,
                "details": _to_serializable(details),
            },
        }
        typer.echo(json.dumps(payload, ensure_ascii=False), err=True)
    else:
        typer.echo(f"ERROR[{code}]: {message}", err=True)
    raise typer.Exit(exit_code)
