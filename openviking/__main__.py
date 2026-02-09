# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Main entry point for `python -m openviking` command."""

import argparse
import sys


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="OpenViking - An Agent-native context database",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # serve command
    serve_parser = subparsers.add_parser("serve", help="Start OpenViking HTTP Server")
    serve_parser.add_argument("--host", type=str, default=None, help="Host to bind to")
    serve_parser.add_argument("--port", type=int, default=None, help="Port to bind to")
    serve_parser.add_argument("--path", type=str, default=None, help="Storage path")
    serve_parser.add_argument("--config", type=str, default=None, help="Config file path")
    serve_parser.add_argument("--api-key", type=str, default=None, help="API key")

    # viewer command
    viewer_parser = subparsers.add_parser("viewer", help="Start OpenViking Viewer")
    viewer_parser.add_argument("--port", type=int, default=8501, help="Viewer port")

    args = parser.parse_args()

    if args.command == "serve":
        from openviking.server.bootstrap import main as serve_main

        # Rebuild sys.argv for serve command
        sys.argv = ["openviking-server"]
        if args.host:
            sys.argv.extend(["--host", args.host])
        if args.port:
            sys.argv.extend(["--port", str(args.port)])
        if args.path:
            sys.argv.extend(["--path", args.path])
        if args.config:
            sys.argv.extend(["--config", args.config])
        if args.api_key:
            sys.argv.extend(["--api-key", args.api_key])
        serve_main()

    elif args.command == "viewer":
        from openviking.tools.viewer import main as viewer_main

        viewer_main()

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
