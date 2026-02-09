#!/usr/bin/env python3
"""
OpenViking Server 启动示例

启动方式:
    uv run server.py
    uv run server.py --api-key your-secret-key
    uv run server.py --port 8000 --path ./my_data
"""

import argparse
import os
import sys

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


def main():
    parser = argparse.ArgumentParser(
        description="Start OpenViking HTTP Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run server.py
  uv run server.py --api-key my-secret-key
  uv run server.py --port 8000 --path ./my_data
  uv run server.py --config ./ov.conf
        """,
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=1933, help="Port to bind to")
    parser.add_argument("--path", default="./data", help="Storage path")
    parser.add_argument("--config", default=None, help="Config file path")
    parser.add_argument("--api-key", default=None, help="API key for authentication")
    args = parser.parse_args()

    # Set config file
    if args.config:
        os.environ["OPENVIKING_CONFIG_FILE"] = args.config
    elif os.path.exists("./ov.conf"):
        os.environ.setdefault("OPENVIKING_CONFIG_FILE", "./ov.conf")

    # Display server info
    info = Table(show_header=False, box=None, padding=(0, 2))
    info.add_column("Key", style="bold cyan")
    info.add_column("Value", style="white")
    info.add_row("Host", args.host)
    info.add_row("Port", str(args.port))
    info.add_row("Storage", args.path)
    info.add_row("Config", args.config or os.environ.get("OPENVIKING_CONFIG_FILE", "(default)"))
    info.add_row("Auth", "enabled" if args.api_key else "[dim]disabled[/dim]")

    console.print()
    console.print(Panel(info, title="OpenViking Server", style="bold green", padding=(1, 2)))
    console.print()

    # Rebuild sys.argv for the bootstrap module
    sys.argv = ["openviking-server"]
    sys.argv.extend(["--host", args.host])
    sys.argv.extend(["--port", str(args.port)])
    sys.argv.extend(["--path", args.path])
    if args.api_key:
        sys.argv.extend(["--api-key", args.api_key])

    from openviking.server.bootstrap import main as serve_main

    serve_main()


if __name__ == "__main__":
    main()
