# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Standalone gateway entry point; it never reads the server's ov.conf."""

import argparse
import asyncio
import json
import os
import secrets
import sys
from pathlib import Path

import aiohttp

from .client import VikingClient, VikingError
from .config import ENVIRONMENT_PREFIX, StandaloneGatewayConfig, normalize_url
from .standalone import build_encryptor, check_openviking, create_standalone_app

CONFIG_ENVIRONMENT = ENVIRONMENT_PREFIX + "CONFIG_FILE"
USER_CONFIG = Path("~/.openviking/gateway.conf")
DEFAULT_CONFIGS = (USER_CONFIG, Path("/etc/openviking/gateway.conf"))


def config_path() -> Path | None:
    """``--config`` (exported by ``main``), then the variable, then the first default file."""
    if path := os.environ.get(CONFIG_ENVIRONMENT):
        return Path(path).expanduser()
    return next((p.expanduser() for p in DEFAULT_CONFIGS if p.expanduser().is_file()), None)


def load_config() -> StandaloneGatewayConfig:
    path = config_path()
    if path and not path.is_file():
        raise SystemExit(f"Gateway config file not found: {path}")
    # As in ov.conf, "$VAR" references resolve from the environment.
    raw = json.loads(os.path.expandvars(path.read_text())) if path else {}
    if not raw.get("openviking_url") and not os.environ.get(ENVIRONMENT_PREFIX + "OPENVIKING_URL"):
        raise SystemExit(
            "OpenViking Gateway needs an OpenViking server URL: run `openviking-gateway init` "
            "or set OPENVIKING_GATEWAY_OPENVIKING_URL"
        )
    return StandaloneGatewayConfig.model_validate(raw)


def factory():
    """uvicorn factory: every worker rebuilds the configuration from the environment."""
    return create_standalone_app(load_config())


async def probe(url: str) -> dict:
    async with aiohttp.ClientSession(trust_env=False) as http:
        return await VikingClient(http, url, "0").health(require_identity=False)


def init(path: Path) -> int:
    """Write gateway.conf interactively; a re-run offers the current values as defaults."""
    from openviking_cli.setup_wizard import (
        _bold,
        _dim,
        _green,
        _mask_secret,
        _prompt_confirm,
        _prompt_required_input,
        _prompt_required_int,
        _red,
        _rule,
        _write_config,
    )

    current = json.loads(path.read_text()) if path.is_file() else {}
    _rule("OpenViking Gateway")
    url = current.get("openviking_url") or "http://127.0.0.1:1933"
    while True:
        url = _prompt_required_input("OpenViking server URL", url)
        if url.startswith("$"):
            break  # Resolved from the environment when the gateway starts.
        try:
            url = normalize_url(url)
        except ValueError as error:
            print(f"  {_red(str(error))}")
            if sys.stdin.isatty():
                continue
            return 1
        try:
            health = asyncio.run(probe(url))
        except VikingError as error:
            print(f"  {_red('OpenViking check failed: ' + error.reason)}")
            # Without a terminal every prompt returns its default; retrying would loop.
            if _prompt_confirm("Enter a different URL?", default=sys.stdin.isatty()):
                continue
        else:
            print(
                f"  {_dim('Auth mode: ' + str(health.get('auth_mode', 'unknown')))}"
                f"  {_dim('Version: ' + str(health.get('version', 'unknown')))}"
            )
        break
    current["openviking_url"] = url
    current["host"] = _prompt_required_input("Listen host", current.get("host", "127.0.0.1"))
    current["port"] = _prompt_required_int("Listen port", current.get("port", 1935))

    key = current.get("management_key", "")
    if _prompt_confirm(
        "Require a management key to change upstreams and profiles?", default=bool(key)
    ):
        if not key or not _prompt_confirm(f"Keep the current key ({_mask_secret(key)})?"):
            key = "ovgm_" + secrets.token_urlsafe(32)
            print(f"\n  {_green('Generated management key — copy it now, it is shown once:')}")
            print(f"\n    {_bold(key)}\n")
        current["management_key"] = key
    else:
        current.pop("management_key", None)

    if not _write_config(current, path):
        return 1
    print(f"\n  {_green('Saved ' + str(path))}")
    start = "openviking-gateway" + ("" if path == USER_CONFIG.expanduser() else f" --config {path}")
    print(f"  {_dim('Start it with: ' + start)}")
    return 0


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config",
        default=argparse.SUPPRESS,
        help="gateway.conf path (default: $OPENVIKING_GATEWAY_CONFIG_FILE, "
        "~/.openviking/gateway.conf, /etc/openviking/gateway.conf)",
    )
    parser = argparse.ArgumentParser(description="OpenViking Gateway", parents=[common])
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("init", parents=[common], help="Create or update gateway.conf")
    # Checked before parsing: the init subcommand would otherwise swallow a removed
    # flag's value (--port 1935) and report it as an invalid command.
    known = ("--config", "--help")
    if flags := [a for a in sys.argv[1:] if a[:2] == "--" and a.split("=")[0] not in known]:
        parser.error(
            f"unrecognized argument {flags[0]}; settings now live in gateway.conf "
            f"(openviking-gateway init) or {ENVIRONMENT_PREFIX}<FIELD> environment variables"
        )
    args = parser.parse_args()
    if config := getattr(args, "config", None):
        os.environ[CONFIG_ENVIRONMENT] = str(Path(config).expanduser().resolve())
    if args.command == "init":
        raise SystemExit(init(Path(os.environ.get(CONFIG_ENVIRONMENT) or USER_CONFIG).expanduser()))
    config = load_config()
    import uvicorn

    asyncio.run(check_openviking(config))
    if encryptor := build_encryptor(config):
        # Load (or create) the root key once, before workers start, so bad encryption
        # settings stop startup and workers never race to create the key file.
        asyncio.run(encryptor.provider.get_root_key())
    uvicorn.run(
        "openviking_gateway.cli:factory",
        factory=True,
        host=config.host,
        port=config.port,
        workers=config.workers,
        timeout_keep_alive=config.keep_alive_seconds,
        log_level="info",
        # Signed upload tokens live in query strings; log request metadata only.
        access_log=False,
    )


if __name__ == "__main__":
    main()
