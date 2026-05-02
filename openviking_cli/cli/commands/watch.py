# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Watch task management commands."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer

WATCHES_FILE = Path.home() / ".openviking" / "watches.json"

watch_app = typer.Typer(help="Watch task management")


def _load_watches() -> dict:
    """Load watches.json, returning empty structure if file does not exist."""
    if not WATCHES_FILE.exists():
        return {"watches": []}
    with open(WATCHES_FILE, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as exc:
            typer.echo(f"Error: watches.json is not valid JSON: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    if "watches" not in data or not isinstance(data["watches"], list):
        return {"watches": []}
    return data


def _save_watches(data: dict) -> None:
    """Persist watches data to disk, creating the directory if needed."""
    WATCHES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(WATCHES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


@watch_app.command("list")
def watch_list_command() -> None:
    """List all configured watch tasks."""
    data = _load_watches()
    watches = data["watches"]

    if not watches:
        typer.echo("No watch tasks configured.")
        return

    # Column widths
    col_path = max(len(w.get("path", "")) for w in watches)
    col_path = max(col_path, len("PATH"))
    col_target = max(len(w.get("target", "") or "") for w in watches)
    col_target = max(col_target, len("TARGET"))
    col_interval = max(len(str(w.get("interval", ""))) for w in watches)
    col_interval = max(col_interval, len("INTERVAL(s)"))
    col_synced = max(len(str(w.get("last_synced", "") or "never")) for w in watches)
    col_synced = max(col_synced, len("LAST_SYNCED"))

    header = (
        f"{'PATH':<{col_path}}  "
        f"{'TARGET':<{col_target}}  "
        f"{'INTERVAL(s)':<{col_interval}}  "
        f"{'LAST_SYNCED':<{col_synced}}"
    )
    separator = "-" * len(header)
    typer.echo(header)
    typer.echo(separator)

    for w in watches:
        last_synced = w.get("last_synced") or "never"
        typer.echo(
            f"{w.get('path', ''):<{col_path}}  "
            f"{(w.get('target') or ''):<{col_target}}  "
            f"{w.get('interval', 300):<{col_interval}}  "
            f"{last_synced:<{col_synced}}"
        )


@watch_app.command("add")
def watch_add_command(
    path: str = typer.Argument(..., help="Local path or URL to watch"),
    to: Optional[str] = typer.Option(None, "--to", help="Target URI (e.g. viking://resources/myrepo)"),
    interval: int = typer.Option(300, "--interval", help="Polling interval in seconds (default: 300)"),
) -> None:
    """Register a path to watch without triggering re-indexing."""
    data = _load_watches()

    for entry in data["watches"]:
        if entry.get("path") == path:
            typer.echo(f"Error: path '{path}' is already being watched.", err=True)
            raise typer.Exit(code=1)

    entry = {
        "path": path,
        "target": to,
        "interval": interval,
        "added_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_synced": None,
    }
    data["watches"].append(entry)
    _save_watches(data)
    typer.echo(f"Added watch for '{path}' (interval: {interval}s).")


@watch_app.command("remove")
def watch_remove_command(
    path: str = typer.Argument(..., help="Path to remove from watch list"),
) -> None:
    """Remove a watch task by path."""
    data = _load_watches()

    original_len = len(data["watches"])
    data["watches"] = [w for w in data["watches"] if w.get("path") != path]

    if len(data["watches"]) == original_len:
        typer.echo(f"Error: no watch task found for path '{path}'.", err=True)
        raise typer.Exit(code=1)

    _save_watches(data)
    typer.echo(f"Removed watch for '{path}'.")


@watch_app.command("set")
def watch_set_command(
    path: str = typer.Argument(..., help="Path whose watch interval should be updated"),
    interval: int = typer.Option(..., "--interval", help="New polling interval in seconds"),
) -> None:
    """Update the polling interval of an existing watch task without re-indexing."""
    data = _load_watches()

    for entry in data["watches"]:
        if entry.get("path") == path:
            old_interval = entry.get("interval")
            entry["interval"] = interval
            _save_watches(data)
            typer.echo(f"Updated interval for '{path}': {old_interval}s -> {interval}s.")
            return

    typer.echo(f"Error: no watch task found for path '{path}'.", err=True)
    raise typer.Exit(code=1)


def register(app: typer.Typer) -> None:
    """Register watch command group."""
    app.add_typer(watch_app, name="watch")
