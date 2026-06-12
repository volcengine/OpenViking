#!/usr/bin/env python3

import argparse
import json
import os
import shlex
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from openviking_cli.utils.config.config_loader import resolve_config_path
from openviking_cli.utils.config.consts import (
    DEFAULT_OV_CONF,
    DEFAULT_OVCLI_CONF,
    OPENVIKING_CLI_CONFIG_ENV,
    OPENVIKING_CONFIG_ENV,
)


def _log(message: str) -> None:
    print(f"[preflight] {message}")


def _error(message: str) -> None:
    print(f"[preflight] {message}", file=sys.stderr)


class UserKeyValidationError(RuntimeError):
    """Raised when the configured OpenViking key is not a usable User key."""


def _load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def _is_interactive() -> bool:
    interactive_env = os.environ.get("INTERACTIVE", "").strip()
    if interactive_env:
        return interactive_env == "1"
    return sys.stdin.isatty() and sys.stdout.isatty()


def _prompt_text(prompt: str) -> str:
    try:
        with open("/dev/tty", "r", encoding="utf-8") as tty_in:
            print(prompt, end="", flush=True)
            return tty_in.readline().strip()
    except Exception:
        return input(prompt).strip()


def _prompt_api_key() -> str:
    return _prompt_text("[preflight] 请输入 OpenViking User API key: ")


def _prompt_root_api_key() -> str:
    return _prompt_text(
        "[preflight] 请输入 OpenViking Root API key，用于自动创建 default User key: "
    )


def _prompt_yes_no(prompt: str, default: bool = False) -> bool:
    if not _is_interactive():
        return False
    suffix = "Y/n" if default else "y/N"
    answer = _prompt_text(f"{prompt} [{suffix}]: ").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes", "1", "true"}


def _resolve_configured_account_hint() -> str:
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
            bot_server_url = str(
                ((data.get("bot") or {}).get("ov_server") or {}).get("server_url") or ""
            ).strip()
            if bot_server_url:
                return bot_server_url.rstrip("/")

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
        _error("未找到 ov.conf，无法读取 OpenViking User API key。")
        raise SystemExit(1)

    try:
        return _load_json(Path(ov_conf_path))
    except Exception as exc:
        _error(f"读取 ov.conf 失败: {exc}")
        raise SystemExit(1)


def _write_json_with_backup(path: Path, data: dict) -> None:
    if path.exists():
        backup = path.with_suffix(path.suffix + ".bak")
        with open(path, "r", encoding="utf-8") as src:
            original = src.read()
        with open(backup, "w", encoding="utf-8") as bak:
            bak.write(original)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _sync_bot_identity(account_id: str, user_id: str, api_key: str) -> None:
    ov_conf_path = resolve_config_path(None, OPENVIKING_CONFIG_ENV, DEFAULT_OV_CONF)
    if ov_conf_path is None:
        return
    path = Path(ov_conf_path)
    ov_data = _load_ov_conf()
    ov_server = ov_data.setdefault("bot", {}).setdefault("ov_server", {})
    changed = False
    desired = {
        "api_key_type": "user",
        "api_key": api_key,
        "account_id": account_id,
        "admin_user_id": user_id,
    }
    for key, value in desired.items():
        if str(ov_server.get(key) or "").strip() != value:
            ov_server[key] = value
            changed = True
    if changed:
        _write_json_with_backup(path, ov_data)
        _log(
            "已同步 bot.ov_server: "
            f"api_key_type=user, account_id={account_id}, admin_user_id={user_id}"
        )


def _resolve_openviking_api_key() -> tuple[str, str]:
    ov_data = _load_ov_conf()
    bot_key = str(((ov_data.get("bot") or {}).get("ov_server") or {}).get("api_key") or "").strip()
    if bot_key:
        return bot_key, "bot.ov_server.api_key"

    path = resolve_config_path(None, OPENVIKING_CLI_CONFIG_ENV, DEFAULT_OVCLI_CONF)
    if path is not None:
        try:
            data = _load_json(Path(path))
        except Exception:
            data = {}
        cli_key = str(data.get("api_key") or "").strip()
        if cli_key:
            return cli_key, "ovcli.conf.api_key"

    if _is_interactive():
        key = _prompt_api_key()
        if key:
            return key, "interactive input"

    return "", "not configured"


def _resolve_root_api_key() -> tuple[str, str]:
    ov_data = _load_ov_conf()
    server_key = str((ov_data.get("server") or {}).get("root_api_key") or "").strip()
    if server_key:
        return server_key, "server.root_api_key"

    bot_legacy_key = str(
        ((ov_data.get("bot") or {}).get("ov_server") or {}).get("root_api_key") or ""
    ).strip()
    if bot_legacy_key:
        return bot_legacy_key, "bot.ov_server.root_api_key"

    if _is_interactive():
        key = _prompt_root_api_key()
        if key:
            return key, "interactive input"

    return "", "not configured"


def _parse_health_identity(body: str) -> tuple[str, str, str]:
    try:
        payload = json.loads(body)
    except Exception as exc:
        raise UserKeyValidationError(f"/health 返回非 JSON: {exc}") from exc

    account_id = str(payload.get("account_id") or "").strip()
    user_id = str(payload.get("user_id") or "").strip()
    role = str(payload.get("role") or "").strip().lower()
    if not account_id or not user_id or not role:
        raise UserKeyValidationError(
            f"API key 未解析出有效身份，请检查 key 是否正确。/health 返回: {payload}"
        )
    return account_id, user_id, role


def _read_http_error(exc: urllib.error.HTTPError) -> str:
    return exc.read().decode("utf-8", errors="replace")


def _admin_request(
    url: str,
    root_api_key: str,
    method: str,
    path: str,
    payload: dict | None = None,
) -> dict:
    body = None
    headers = {
        "X-API-Key": root_api_key,
        "Content-Type": "application/json",
    }
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{url}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = _read_http_error(exc)
        raise UserKeyValidationError(f"Admin API 请求失败（HTTP {exc.code}）: {detail}") from exc
    except Exception as exc:
        raise UserKeyValidationError(f"Admin API 请求失败: {exc}") from exc

    try:
        parsed = json.loads(raw)
    except Exception as exc:
        raise UserKeyValidationError(f"Admin API 返回非 JSON: {raw}") from exc
    if parsed.get("status") != "ok":
        raise UserKeyValidationError(f"Admin API 返回失败: {parsed}")
    result = parsed.get("result")
    return result if isinstance(result, dict) else {"items": result}


def _ensure_default_user_key(url: str, root_api_key: str) -> tuple[str, str, str]:
    account_id = "default"
    user_id = "default"
    admin_user_id = "admin"
    quoted_account = urllib.parse.quote(account_id, safe="")
    quoted_user = urllib.parse.quote(user_id, safe="")

    accounts = _admin_request(url, root_api_key, "GET", "/api/v1/admin/accounts").get("items") or []
    account_exists = any(
        item.get("account_id") == account_id for item in accounts if isinstance(item, dict)
    )
    if not account_exists:
        _log("default account 不存在，将使用 Root key 创建 account=default。")
        _admin_request(
            url,
            root_api_key,
            "POST",
            "/api/v1/admin/accounts",
            {"account_id": account_id, "admin_user_id": admin_user_id},
        )

    users = (
        _admin_request(
            url,
            root_api_key,
            "GET",
            f"/api/v1/admin/accounts/{quoted_account}/users",
        ).get("items")
        or []
    )
    default_user = next(
        (item for item in users if isinstance(item, dict) and item.get("user_id") == user_id),
        None,
    )
    if default_user is None:
        _log("default User 不存在，将注册 account=default, user=default, role=user。")
        created = _admin_request(
            url,
            root_api_key,
            "POST",
            f"/api/v1/admin/accounts/{quoted_account}/users",
            {"user_id": user_id, "role": "user"},
        )
        user_key = str(created.get("user_key") or "").strip()
    else:
        role = str(default_user.get("role") or "").strip().lower()
        if role != "user":
            _log(f"default User 当前 role={role or 'unknown'}，将调整为 role=user。")
            _admin_request(
                url,
                root_api_key,
                "PUT",
                f"/api/v1/admin/accounts/{quoted_account}/users/{quoted_user}/role",
                {"role": "user"},
            )
        regenerated = _admin_request(
            url,
            root_api_key,
            "POST",
            f"/api/v1/admin/accounts/{quoted_account}/users/{quoted_user}/key",
            {},
        )
        user_key = str(regenerated.get("user_key") or "").strip()

    if not user_key:
        raise UserKeyValidationError("Admin API 未返回 user_key，无法继续评测。")
    return account_id, user_id, user_key


def _ensure_server_and_user_key_ready(
    url: str, selected_account: str, api_key: str
) -> tuple[str, str]:
    req = urllib.request.Request(
        f"{url}/health",
        headers={
            "X-API-Key": api_key,
            "Content-Type": "application/json",
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        detail = _read_http_error(e)
        raise UserKeyValidationError(
            f"OpenViking server 检查失败（HTTP {e.code}）: {detail}"
        ) from e
    except Exception as exc:
        raise UserKeyValidationError(f"OpenViking server 不可用: {exc}") from exc

    account_id, user_id, role = _parse_health_identity(body)
    if role != "user":
        raise UserKeyValidationError(f"当前 API key 解析为 role={role}。评测需要普通 User key。")

    if selected_account and selected_account != "default" and selected_account != account_id:
        _log(
            f"ovcli.conf.account={selected_account} "
            f"与 API key 归属 account={account_id} 不一致；"
            "本次评测使用 API key 归属 account。"
        )

    _log(
        "OpenViking server 可用，User key 身份: "
        f"account={account_id}, user={user_id}, role={role}。"
    )
    return account_id, user_id


def _resolve_ready_user_identity(
    openviking_url: str,
    selected_account: str,
    api_key: str,
    key_source: str,
) -> tuple[str, str, str]:
    if api_key:
        _log(f"使用 {key_source} 校验 OpenViking User key")
        try:
            account, user_id = _ensure_server_and_user_key_ready(
                openviking_url, selected_account, api_key
            )
            return account, user_id, api_key
        except UserKeyValidationError as exc:
            _error(str(exc))
            prompt = (
                "[preflight] 当前 User key 不可用，是否使用 Root key 自动生成 default User API key"
            )
    else:
        _error("未配置 OpenViking User API key。")
        prompt = "[preflight] 是否使用 Root key 自动生成 default User API key"

    if not _prompt_yes_no(prompt, default=False):
        _error("请配置可用的 bot.ov_server.api_key 或 ovcli.conf.api_key 后重试。")
        raise SystemExit(1)

    root_api_key, root_key_source = _resolve_root_api_key()
    if not root_api_key:
        _error(
            "未配置 OpenViking Root API key，无法自动生成 User key。"
            "请设置 server.root_api_key 后重试。"
        )
        raise SystemExit(1)

    try:
        _log(f"使用 {root_key_source} 自动生成/刷新 default User key。")
        account, user_id, user_key = _ensure_default_user_key(openviking_url, root_api_key)
        checked_account, checked_user_id = _ensure_server_and_user_key_ready(
            openviking_url, selected_account, user_key
        )
    except UserKeyValidationError as exc:
        _error(str(exc))
        raise SystemExit(1) from exc
    _log("已生成可用的 default User API key。")
    return checked_account or account, checked_user_id or user_id, user_key


def _write_env_file(
    path: Path, account: str, openviking_url: str, api_key: str, user_id: str
) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"ACCOUNT={shlex.quote(account)}\n")
        f.write(f"OPENVIKING_URL={shlex.quote(openviking_url)}\n")
        f.write(f"OPENVIKING_API_KEY={shlex.quote(api_key)}\n")
        f.write(f"OPENVIKING_USER={shlex.quote(user_id)}\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resolve runtime eval account/url and validate OpenViking readiness"
    )
    parser.add_argument(
        "--output-env-file",
        required=True,
        help="File path to write ACCOUNT/OPENVIKING_URL exports",
    )
    args = parser.parse_args()

    selected_account = _resolve_configured_account_hint()
    openviking_url = _resolve_openviking_url()
    api_key, key_source = _resolve_openviking_api_key()

    _log(f"本次导入使用 OpenViking URL: {openviking_url}")

    account, user_id, api_key = _resolve_ready_user_identity(
        openviking_url, selected_account, api_key, key_source
    )
    _sync_bot_identity(account, user_id, api_key)
    _write_env_file(Path(args.output_env_file), account, openviking_url, api_key, user_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
