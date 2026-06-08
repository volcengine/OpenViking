#!/usr/bin/env python3
"""Provision a benchmark runtime user and write a VikingBot user-key config."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

SCRIPT_DIR = Path(__file__).resolve().parent
BENCHMARK_DIR = SCRIPT_DIR.parent


class AdminAPIError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"Admin API returned HTTP {status}: {body}")
        self.status = status
        self.body = body


def _load_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _infer_server_url(config: dict[str, Any], explicit: str | None) -> str:
    if explicit:
        return explicit.rstrip("/")

    bot_server = config.get("bot", {}).get("ov_server", {})
    if isinstance(bot_server, dict) and bot_server.get("server_url"):
        return str(bot_server["server_url"]).rstrip("/")

    server = config.get("server", {})
    if isinstance(server, dict):
        host = server.get("host") or "127.0.0.1"
        port = server.get("port") or 1933
        return f"http://{host}:{port}".rstrip("/")

    return "http://127.0.0.1:1933"


def _request_json(
    method: str,
    url: str,
    api_key: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = None
    headers = {
        "Accept": "application/json",
        "X-API-Key": api_key,
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise AdminAPIError(exc.code, body) from exc

    return json.loads(raw) if raw else {}


def _extract_user_key(response: dict[str, Any]) -> str:
    result = response.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(f"Admin API response missing result object: {response}")
    user_key = result.get("user_key")
    if not isinstance(user_key, str) or not user_key:
        raise RuntimeError(
            "Admin API did not return a user_key. "
            "Provisioning user-key configs is only supported in api_key mode."
        )
    return user_key


def _provision_user(
    *,
    server_url: str,
    provision_api_key: str,
    account: str,
    user: str,
    role: str,
) -> str:
    account_path = quote(account, safe="")
    user_path = quote(user, safe="")
    users_url = f"{server_url}/api/v1/admin/accounts/{account_path}/users"

    try:
        response = _request_json(
            "POST",
            users_url,
            provision_api_key,
            {"user_id": user, "role": role},
        )
        return _extract_user_key(response)
    except AdminAPIError as exc:
        already_exists = exc.status in {400, 409} and "exist" in exc.body.lower()
        if not already_exists:
            raise

    regenerate_url = f"{users_url}/{user_path}/key"
    response = _request_json("POST", regenerate_url, provision_api_key)
    return _extract_user_key(response)


def _write_runtime_config(
    *,
    base_config: dict[str, Any],
    output: Path,
    server_url: str,
    account: str,
    user: str,
    user_key: str,
) -> None:
    config = dict(base_config)
    server = dict(config.get("server") or {})
    if "root_api_key" in server:
        server["root_api_key"] = ""
        config["server"] = server

    bot = dict(config.get("bot") or {})
    ov_server = dict(bot.get("ov_server") or {})
    ov_server.update(
        {
            "mode": "remote",
            "api_key_type": "user",
            "server_url": server_url,
            # Historical VikingBot field name: in user-key mode this stores the user key.
            "root_api_key": user_key,
            "account_id": account,
            "admin_user_id": user,
        }
    )
    bot["ov_server"] = ov_server
    config["bot"] = bot

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create an OpenViking benchmark user and write a user-key ov.conf."
    )
    parser.add_argument(
        "--user",
        required=True,
        help="Benchmark runtime user id, e.g. tau2_airline_v0",
    )
    parser.add_argument("--account", default="default", help="OpenViking account id")
    parser.add_argument("--role", default="user", help="Role for newly registered users")
    parser.add_argument("--server-url", default=None, help="OpenViking server URL")
    parser.add_argument(
        "--provision-api-key",
        default=os.environ.get("OPENVIKING_PROVISION_API_KEY"),
        help="Root/admin key used only for Admin API provisioning",
    )
    parser.add_argument(
        "--base-config",
        default=os.environ.get("OPENVIKING_CONFIG_FILE"),
        help="Base ov.conf to copy non-identity bot settings from",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output ov.conf path. Default: benchmark/tau2/vikingbot/.generated/<user>.ov.conf",
    )
    args = parser.parse_args()

    if not args.provision_api_key:
        raise SystemExit("Missing --provision-api-key or OPENVIKING_PROVISION_API_KEY")

    base_config_path = Path(args.base_config).expanduser() if args.base_config else None
    base_config = _load_json(base_config_path)
    server_url = _infer_server_url(base_config, args.server_url)
    output = (
        Path(args.out).expanduser()
        if args.out
        else BENCHMARK_DIR / ".generated" / f"{args.user}.ov.conf"
    )

    user_key = _provision_user(
        server_url=server_url,
        provision_api_key=args.provision_api_key,
        account=args.account,
        user=args.user,
        role=args.role,
    )
    _write_runtime_config(
        base_config=base_config,
        output=output,
        server_url=server_url,
        account=args.account,
        user=args.user,
        user_key=user_key,
    )
    print(output)


if __name__ == "__main__":
    main()
