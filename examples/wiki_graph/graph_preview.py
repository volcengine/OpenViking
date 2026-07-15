# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Generate and open an HTML preview of stored Wiki and resource links."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import webbrowser
from pathlib import Path

import httpx

from openviking_cli.client.http import AsyncHTTPClient
from openviking_cli.utils.config.consts import OPENVIKING_CLI_CONFIG_ENV

DEFAULT_URL = "http://127.0.0.1:1933"
DEFAULT_SPACES = ["viking://resources", "viking://user/memories/entities"]
DEFAULT_LOCAL_OUTPUT = Path.home() / "Desktop" / "ov_graph" / "openviking_graph.html"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build an HTML graph from existing OpenViking links and open it."
    )
    parser.add_argument(
        "space_uris",
        nargs="*",
        default=DEFAULT_SPACES,
        help="Viking roots to include (default: resources and current-user entities)",
    )
    parser.add_argument("--config", help="Path to ovcli.conf")
    parser.add_argument("--url", help="OpenViking server URL")
    parser.add_argument(
        "--output",
        help="Local HTML output path (default: ~/Desktop/ov_graph/openviking_graph.html)",
    )
    parser.add_argument("--no-open", action="store_true", help="Do not open a browser")
    return parser


def _local_output(path: str | None) -> Path:
    output = Path(path).expanduser().resolve() if path else DEFAULT_LOCAL_OUTPUT
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


async def _generate_graph(args: argparse.Namespace) -> bytes:
    client = AsyncHTTPClient(url=args.url)
    await client.initialize()
    try:
        response = await client._request(
            "POST",
            "/api/v1/relations/build_graph",
            json={"space_uris": args.space_uris},
        )
        response.raise_for_status()
        html = response.json().get("result", {}).get("html")
        if not isinstance(html, str) or not html:
            raise ValueError("build_graph response did not contain HTML")
        return html.encode("utf-8")
    finally:
        await client.close()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    previous_config = os.environ.get(OPENVIKING_CLI_CONFIG_ENV)
    try:
        if args.config:
            config_path = Path(args.config).expanduser()
            if not config_path.is_file():
                raise FileNotFoundError(f"CLI config not found: {config_path}")
            os.environ[OPENVIKING_CLI_CONFIG_ENV] = str(config_path)
        elif (
            not previous_config
            and not os.environ.get("OPENVIKING_URL")
            and not (Path.home() / ".openviking" / "ovcli.conf").is_file()
        ):
            args.url = args.url or DEFAULT_URL

        output = _local_output(args.output)
        output.write_bytes(asyncio.run(_generate_graph(args)))
        print(f"Graph preview: {output}")
        if not args.no_open and not webbrowser.open(output.as_uri(), new=2):
            print("Could not open a browser automatically; open the file above.", file=sys.stderr)
        return 0
    except (httpx.HTTPError, OSError, TypeError, ValueError) as exc:
        print(f"Failed to generate graph preview: {exc}", file=sys.stderr)
        return 1
    finally:
        if args.config:
            if previous_config is None:
                os.environ.pop(OPENVIKING_CLI_CONFIG_ENV, None)
            else:
                os.environ[OPENVIKING_CLI_CONFIG_ENV] = previous_config


if __name__ == "__main__":
    raise SystemExit(main())
