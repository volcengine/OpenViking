#!/usr/bin/env python3

import argparse
import json
import os
import shlex
import sys
import urllib.error
import urllib.request
from pathlib import Path

from openviking_cli.utils.config.config_loader import resolve_config_path
from openviking_cli.utils.config.consts import (
    DEFAULT_OVCLI_CONF,
    DEFAULT_OV_CONF,
    OPENVIKING_CLI_CONFIG_ENV,
    OPENVIKING_CONFIG_ENV,
)


def _log(message: str) -> None:
    print(f"[preflight] {message}")


def _error(message: str) -> None:
    print(f"[preflight] {message}", file=sys.stderr)


def _load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def _resolve_account() -> str:
    path = resolve_config_path(None, OPENVIKING_CLI_CONFIG_ENV, DEFAULT_OVCLI_CONF)
    if path is None:
        return "default"
    try:
        data = _load_json(Path(path))
    except Exception:
        return "default"
    account = str(data.get("account") or "").strip()
    return account or "default"


def _resolve_openviking_url() -> str:
    host = "127.0.0.1"
    port = 1933

    path = resolve_config_path(None, OPENVIKING_CONFIG_ENV, DEFAULT_OV_CONF)
    if path is not None:
        try:
            data = _load_json(Path(path))
            server = data.get("server") or {}
            parsed_host = str(server.get("host") or "").strip()
            parsed_port = server.get("port")
            if parsed_host:
                host = parsed_host
            if isinstance(parsed_port, int):
                port = parsed_port
            elif isinstance(parsed_port, str) and parsed_port.strip().isdigit():
                port = int(parsed_port.strip())
        except Exception:
            pass

    return f"http://{host}:{port}"


def _load_ov_conf() -> dict:
    ov_conf_path = resolve_config_path(None, OPENVIKING_CONFIG_ENV, DEFAULT_OV_CONF)
    if ov_conf_path is None:
        _error("未找到 ov.conf，无法读取 root_api_key。")
        raise SystemExit(1)

    try:
        return _load_json(Path(ov_conf_path))
    except Exception as exc:
        _error(f"读取 ov.conf 失败: {exc}")
        raise SystemExit(1)


def _parse_accounts(body: str) -> list:
    try:
        payload = json.loads(body)
    except Exception as exc:
        _error(f"/api/v1/admin/accounts 返回非 JSON: {exc}")
        raise SystemExit(1)

    accounts = payload.get("result", payload)
    if not isinstance(accounts, list):
        _error("/api/v1/admin/accounts 返回格式异常。")
        raise SystemExit(1)
    return accounts


def _account_exists(accounts: list, account: str) -> bool:
    for item in accounts:
        if isinstance(item, dict):
            account_id = str(item.get("account_id") or item.get("id") or "").strip()
            if account_id == account:
                return True
        elif isinstance(item, str) and item == account:
            return True
    return False


def _prompt_confirm_create(account: str) -> bool:
    prompt = f"[preflight] account '{account}' 不存在，是否自动创建该 account? [Y/n]: "
    try:
        with open("/dev/tty", "r", encoding="utf-8") as tty_in:
            print(prompt, end="", flush=True)
            answer = tty_in.readline().strip().lower()
    except Exception as exc:
        _error(f"无法读取终端输入: {exc}")
        raise SystemExit(1)
    return answer in ("", "y", "yes")


def _ensure_server_and_account_ready(url: str, account: str, interactive: bool) -> None:
    ov_data = _load_ov_conf()

    root_key = str((ov_data.get("server") or {}).get("root_api_key") or "").strip()
    if not root_key:
        _error("server.root_api_key 为空，无法执行服务连通性检查。")
        raise SystemExit(1)

    admin_user_id = str(
        ((ov_data.get("bot") or {}).get("ov_server") or {}).get("admin_user_id") or "default"
    ).strip() or "default"

    req = urllib.request.Request(
        f"{url}/api/v1/admin/accounts",
        headers={
            "X-API-Key": root_key,
            "Content-Type": "application/json",
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        _error(f"OpenViking server 检查失败（HTTP {e.code}）: {detail}")
        raise SystemExit(1)
    except Exception as exc:
        _error(f"OpenViking server 不可用: {exc}")
        raise SystemExit(1)

    accounts = _parse_accounts(body)
    if _account_exists(accounts, account):
        _log(f"OpenViking server 可用，account '{account}' 已就绪。")
        return

    if not interactive:
        _error(f"account '{account}' 不存在，非交互模式下不会自动创建。")
        raise SystemExit(1)

    if not _prompt_confirm_create(account):
        _error("已取消自动创建 account。")
        raise SystemExit(1)

    create_req = urllib.request.Request(
        f"{url}/api/v1/admin/accounts",
        headers={
            "X-API-Key": root_key,
            "Content-Type": "application/json",
        },
        data=json.dumps({"account_id": account, "admin_user_id": admin_user_id}).encode("utf-8"),
        method="POST",
    )

    try:
        with urllib.request.urlopen(create_req, timeout=10) as create_resp:
            create_body = create_resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        _error(f"创建 account 失败（HTTP {e.code}）: {detail}")
        raise SystemExit(1)
    except Exception as exc:
        _error(f"创建 account 失败: {exc}")
        raise SystemExit(1)

    try:
        create_payload = json.loads(create_body)
    except Exception:
        create_payload = {}

    if isinstance(create_payload, dict) and create_payload.get("status") == "error":
        _error(f"创建 account 失败: {create_payload}")
        raise SystemExit(1)

    _log(f"已创建 account '{account}'（admin_user_id={admin_user_id}）。")
    _log(f"OpenViking server 可用，account '{account}' 已就绪。")


def _write_env_file(path: Path, account: str, openviking_url: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"ACCOUNT={shlex.quote(account)}\n")
        f.write(f"OPENVIKING_URL={shlex.quote(openviking_url)}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve runtime eval account/url and validate OpenViking readiness")
    parser.add_argument("--output-env-file", required=True, help="File path to write ACCOUNT/OPENVIKING_URL exports")
    args = parser.parse_args()

    interactive_env = os.environ.get("INTERACTIVE", "").strip()
    if interactive_env:
        interactive = interactive_env == "1"
    else:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()

    account = _resolve_account()
    openviking_url = _resolve_openviking_url()

    _log(f"本次导入与评测使用 account: {account}")
    _log(f"本次导入使用 OpenViking URL: {openviking_url}")

    _ensure_server_and_account_ready(openviking_url, account, interactive)
    _write_env_file(Path(args.output_env_file), account, openviking_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
