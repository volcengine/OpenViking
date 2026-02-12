#!/usr/bin/env python3
"""
OpenViking 同步客户端示例 (HTTP mode)

使用 SyncHTTPClient 通过 HTTP 连接远程 Server，演示完整 API。

前置条件:
    先启动 Server: openviking serve

运行:
    uv run client_sync.py
    uv run client_sync.py --url http://localhost:1933
    uv run client_sync.py --api-key your-secret-key
"""

import argparse
import sys
import threading

from rich import box
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

import openviking as ov
from openviking_cli.utils.async_utils import run_async

console = Console()
PANEL_WIDTH = 78


def _bool_mark(value) -> str:
    return "[green]Yes[/green]" if value else "[red]No[/red]"


def spin(message: str, func, *args, **kwargs):
    """Run func with a spinner."""
    spinner = Spinner("dots", text=message)
    result = None
    error = None

    def _run():
        nonlocal result, error
        try:
            result = func(*args, **kwargs)
        except Exception as e:
            error = e

    t = threading.Thread(target=_run)
    t.start()
    with Live(spinner, console=console, refresh_per_second=10, transient=True):
        t.join()
    if error:
        raise error
    return result


def main():
    parser = argparse.ArgumentParser(description="OpenViking sync client example")
    parser.add_argument("--url", default="http://localhost:1933", help="Server URL")
    parser.add_argument("--api-key", default=None, help="API key")
    args = parser.parse_args()

    client = ov.SyncHTTPClient(url=args.url, api_key=args.api_key)

    try:
        # ── Connect ──
        spin("Connecting...", client.initialize)
        console.print(
            Panel(
                f"Connected to [bold cyan]{args.url}[/bold cyan]",
                style="green",
                width=PANEL_WIDTH,
            )
        )
        console.print()

        # ── System Status ──
        console.print(Panel("System Status", style="bold magenta", width=PANEL_WIDTH))
        status = client.get_status()
        status_table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
        status_table.add_column("Component", style="cyan")
        status_table.add_column("Healthy", justify="center")
        status_table.add_row("Overall", _bool_mark(status.get("is_healthy")))
        for name, info in status.get("components", {}).items():
            status_table.add_row(f"  {name}", _bool_mark(info.get("is_healthy")))
        console.print(status_table)
        console.print()

        # ── Add Resource ──
        console.print(Panel("Add Resource", style="bold magenta", width=PANEL_WIDTH))
        result = spin(
            "Adding resource...",
            client.add_resource,
            path="https://raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/README.md",
            reason="demo resource",
        )
        root_uri = result.get("root_uri", "")
        console.print(f"  Resource: [bold]{root_uri}[/bold]")
        spin("Waiting for processing...", client.wait_processed, timeout=120)
        console.print("  [green]Processing complete[/green]")
        console.print()

        # ── File System ──
        console.print(Panel("File System", style="bold magenta", width=PANEL_WIDTH))
        entries = client.ls("viking://")
        fs_table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
        fs_table.add_column("Name", style="cyan")
        fs_table.add_column("Type", style="dim")
        for entry in entries:
            if isinstance(entry, dict):
                fs_table.add_row(
                    entry.get("name", "?"),
                    "dir" if entry.get("isDir") else "file",
                )
            else:
                fs_table.add_row(str(entry), "")
        console.print(fs_table)

        tree = client.tree("viking://")
        tree_nodes = tree if isinstance(tree, list) else tree.get("children", [])
        console.print(f"  Tree nodes: [bold]{len(tree_nodes)}[/bold]")
        console.print()

        # ── Content ──
        if root_uri:
            console.print(Panel("Content", style="bold magenta", width=PANEL_WIDTH))
            abstract = client.abstract(root_uri)
            console.print(
                Panel(
                    Text(abstract[:300] + ("..." if len(abstract) > 300 else ""), style="white"),
                    title="Abstract",
                    style="dim",
                    width=PANEL_WIDTH,
                )
            )
            overview = client.overview(root_uri)
            console.print(
                Panel(
                    Text(overview[:300] + ("..." if len(overview) > 300 else ""), style="white"),
                    title="Overview",
                    style="dim",
                    width=PANEL_WIDTH,
                )
            )
            console.print()

        # ── Semantic Search (find) ──
        console.print(Panel("Semantic Search", style="bold magenta", width=PANEL_WIDTH))
        results = spin("Searching...", client.find, "what is openviking", limit=3)
        if hasattr(results, "resources") and results.resources:
            search_table = Table(
                box=box.ROUNDED,
                show_header=True,
                header_style="bold green",
            )
            search_table.add_column("#", style="cyan", width=4)
            search_table.add_column("URI", style="white")
            search_table.add_column("Score", style="bold green", justify="right")
            for i, r in enumerate(results.resources, 1):
                search_table.add_row(str(i), r.uri, f"{r.score:.4f}")
            console.print(search_table)
        else:
            console.print("  [dim]No results[/dim]")
        console.print()

        # ── Grep & Glob ──
        console.print(Panel("Grep & Glob", style="bold magenta", width=PANEL_WIDTH))
        grep_result = client.grep(uri="viking://", pattern="OpenViking")
        grep_count = len(grep_result) if isinstance(grep_result, list) else grep_result
        console.print(f"  Grep 'OpenViking': [bold]{grep_count}[/bold] matches")

        glob_result = client.glob(pattern="**/*.md")
        glob_count = len(glob_result) if isinstance(glob_result, list) else glob_result
        console.print(f"  Glob '**/*.md':    [bold]{glob_count}[/bold] matches")
        console.print()

        # ── Session + Context Search ──
        console.print(Panel("Session & Context Search", style="bold magenta", width=PANEL_WIDTH))
        session = client.session()
        console.print(f"  Created session: [bold]{session.session_id}[/bold]")

        run_async(
            session.add_message(
                role="user",
                content="Tell me about OpenViking",
            )
        )
        run_async(
            session.add_message(
                role="assistant",
                content="OpenViking is an agent-native context database.",
            )
        )
        console.print("  Added [bold]2[/bold] messages")

        ctx_results = spin(
            "Searching with session context...",
            client.search,
            "how to use it",
            session_id=session.session_id,
            limit=3,
        )
        if hasattr(ctx_results, "resources") and ctx_results.resources:
            for r in ctx_results.resources:
                console.print(f"  [cyan]{r.uri}[/cyan] (score: [green]{r.score:.4f}[/green])")
        else:
            console.print("  [dim]No context search results[/dim]")

        run_async(session.delete())
        console.print(f"  Deleted session: [dim]{session.session_id}[/dim]")
        console.print()

        # ── Relations ──
        console.print(Panel("Relations", style="bold magenta", width=PANEL_WIDTH))
        entries = client.ls("viking://", simple=True)
        if len(entries) >= 2:
            uri_a = entries[0] if isinstance(entries[0], str) else entries[0].get("uri", "")
            uri_b = entries[1] if isinstance(entries[1], str) else entries[1].get("uri", "")
            if uri_a and uri_b:
                client.link(uri_a, uri_b, reason="demo link")
                rels = client.relations(uri_a)
                rel_table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
                rel_table.add_column("Source", style="cyan")
                rel_table.add_column("Target", style="white")
                rel_table.add_column("Count", style="dim", justify="right")
                rel_count = len(rels) if isinstance(rels, list) else rels
                rel_table.add_row(uri_a, uri_b, str(rel_count))
                console.print(rel_table)
                client.unlink(uri_a, uri_b)
                console.print("  [dim]Link removed[/dim]")
        else:
            console.print("  [dim]Need >= 2 resources for relation demo[/dim]")
        console.print()

        # ── Observer ──
        console.print(Panel("Observer", style="bold magenta", width=PANEL_WIDTH))
        observer = client.observer
        obs_table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
        obs_table.add_column("Component", style="cyan")
        obs_table.add_column("Healthy", justify="center")
        obs_table.add_row("Queue", _bool_mark(observer.queue.get("is_healthy")))
        obs_table.add_row("VikingDB", _bool_mark(observer.vikingdb.get("is_healthy")))
        obs_table.add_row("VLM", _bool_mark(observer.vlm.get("is_healthy")))
        obs_table.add_row("System", _bool_mark(observer.system.get("is_healthy")))
        console.print(obs_table)
        console.print()

        # ── Done ──
        console.print(
            Panel(
                "[bold green]All operations completed[/bold green]",
                style="green",
                width=PANEL_WIDTH,
            )
        )

    except Exception as e:
        console.print(
            Panel(
                f"[bold red]Error:[/bold red] {e}",
                style="red",
                width=PANEL_WIDTH,
            )
        )
        import traceback

        traceback.print_exc()
        sys.exit(1)

    finally:
        client.close()


if __name__ == "__main__":
    main()
